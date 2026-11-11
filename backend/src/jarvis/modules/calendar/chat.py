import re
from datetime import date, datetime, time, timedelta

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from jarvis.modules.assistant.dates import (
    DAY_NUMBER,
    MONTHS,
    ConflictingDates,
    day_number,
    read_day,
)
from jarvis.modules.assistant.intent import WHEN, fold, split_fields, tidy
from jarvis.modules.assistant.timing import CLOCK, DURATION, NUMBER, delivery_time, time_prefix
from jarvis.modules.calendar import destinations, service
from jarvis.modules.calendar.models import CalendarFocus, CalendarSource, Entry
from jarvis.modules.calendar.notices import stage
from jarvis.modules.calendar.periods import (
    command_text,
    is_query,
    month_anchor,
    postposed_event,
    requested_period,
)
from jarvis.modules.calendar.scope import DOTS, LEGEND, PROVIDERS, request_scope
from jarvis.modules.calendar.service import ZONE, EventInput, RoutineInput
from jarvis.modules.errors import Problem
from jarvis.modules.messaging.queue import Turn

CREATE = re.compile(
    r"^(?:(?P<rem>me lembr[ae]|lembre-me|crie (?:um )?lembrete)\b(?: de\b| para\b)?(?: que\b)?|(?:marque|marca|marcar|agende|agenda|agendar|adicione|adicionar|insira|inserir|inclua|incluir|coloque|colocar|anote|registr[ae]|registrar|cadastre|cadastrar|crie|criar|bote|botar|ponha|p[oõ]e|salve|salvar) (?!.*?\b(?:mensagem|envio|recado)\b)(?:(?:n[ao] (?:minha? |meu )?(?:agenda|calendario)|um compromisso|o compromisso|um evento)\b\s*)?)\s*"
)
CHANGE = re.compile(
    r"^(cancele|cancela|remarque|reagende|desmarque) (?:o )?(compromisso|lembrete|evento)\s+#?([\da-f-]{8,36})(?:\s+(.*))?$"
)


# Um turno só pode efetivar uma operação incondicional. Verifica o original
# antes que a remoção de prefixos como “quero saber” transforme um exemplo em ordem.
QUALIFIED = re.compile(
    r"\b(?:se|caso|talvez|dependendo|como exemplo|por exemplo|hipoteticamente|"
    r"(?:quero|gostaria de|queria) saber|ainda estou decidindo|"
    r"nao (?:faca|marque|agende|registre|salve|crie|cancele|altere)|"
    r"(?:e|mas|depois|depois disso) (?:cancele|desmarque|remarque|agende|marque|adicione|envie|mande))\b"
)
CLARIFY_ACTION = "Qual ação você confirma? Diga um compromisso e a data para registrar, ou informe o código para cancelar."
REPORTED = re.compile(
    r'["“”«»‘’`]|(?:^|\s)\'|\b(?:mensage(?:m|ns)|recados?|whatsapp|envi[ae]r?|mand[ae]r?|'
    r"avis[ae]r?|dig[ao] (?:a|ao|para|pra)|exemplos?|hipotese|suponha|"
    r"supondo|imagine|simule|traduza|traduzir|citou|disse|pediu|perguntou|"
    r"nao (?:quero|precis[ao]|e para|deve|vai|voce|marque|registre|agende|coloque|crie|reserve))\b"
)


def listing(db, turn, now, normal, unavailable):
    scope = request_scope(normal, query=True)
    if scope.problem:
        return scope.problem
    day, period = requested_period(
        normal,
        (turn.received_at or now).astimezone(ZONE).date(),
        db.get(CalendarFocus, turn.chat_id),
    )
    db.merge(CalendarFocus(route=turn.chat_id, anchor=day, period=period))
    start, end = service.bounds(day, period)
    heading = (
        f"Sua agenda de {start:%d/%m/%Y} (Manaus)"
        if period == "day"
        else f"Sua agenda de {start:%d/%m} a {end - timedelta(days=1):%d/%m/%Y} (Manaus)"
    )
    stmt = select(CalendarSource).where(CalendarSource.selected)
    if scope.providers:
        stmt = stmt.where(CalendarSource.provider.in_(scope.providers))
    sources = db.scalars(stmt).all()
    requested = {PROVIDERS[key] for key in scope.providers}
    issues = set(unavailable) & requested if requested else set(unavailable)
    issues.update(requested - {PROVIDERS[s.provider] for s in sources})
    names = {s.id: PROVIDERS[s.provider] for s in sources}
    for source in sources:
        if source.error or not source.available or not source.last_sync:
            issues.add(names[source.id])
    if issues:
        heading += (
            "\nConsulta parcial. Não consegui confirmar todos os dados de "
            + " e ".join(sorted(issues))
            + "."
        )
        if missing := requested - set(names.values()):
            heading += (
                "\nConecte ou selecione uma agenda de leitura de "
                + " e ".join(sorted(missing))
                + " em Agenda pessoal."
            )
        for provider in sorted(issues):
            stamps = [s.last_sync for s in sources if names[s.id] == provider and s.last_sync]
            if stamps:
                heading += f"\n{provider}: última leitura das agendas disponíveis em {min(stamps).astimezone(ZONE):%d/%m às %H:%M}."
    elif names:
        heading += "\nAgendas consultadas: " + " e ".join(sorted(set(names.values()))) + "."
    else:
        heading += "\nAgenda local."
    pages = [heading]
    week = None
    previous_day = None
    has_local = False
    for item in service.occurrences(db, start, end, providers=scope.providers):
        dt = item.start.astimezone(ZONE)
        label = "dia inteiro" if item.entry.all_day else dt.strftime("%H:%M")
        until = item.end.astimezone(ZONE)
        if item.entry.all_day:
            last = until - timedelta(seconds=1)
            if last.date() != dt.date():
                label += f" até {last:%d/%m}"
        elif item.entry.kind == "event":
            label += until.strftime("–%H:%M" if until.date() == dt.date() else " até %d/%m %H:%M")
        title = " ".join(item.entry.title.split())[:300]
        line = f"{dt:%d/%m} · {label} · {title} · #{str(item.entry.id)[:8]}"
        states = {names[copy.source_id]: copy.state for copy in item.copies if copy.source_id}
        badges = "".join(DOTS[key] for key, name in PROVIDERS.items() if name in states)
        if not badges:
            badges = "⚪"
            has_local = True
        line = badges + " " + line
        for origin, state in states.items():
            if state in ("create", "update", "conflict"):
                line += f" · {origin} ({'conflito' if state == 'conflict' else 'pendente'})"
        gap = "\n" if dt.date() == previous_day else "\n\n"
        if (period == "month" and week is not None and dt.isocalendar()[:2] != week) or len(
            pages[-1]
        ) + len(gap) + len(line) > 3700:
            pages.append("Agenda (continuação)")
            gap = "\n\n"
        pages[-1] += gap + line
        previous_day = dt.date()
        week = dt.isocalendar()[:2]
    if week is None:
        pages[0] += (
            "\nNenhum item nos dados disponíveis para o período."
            if issues
            else "\nNenhum compromisso ou lembrete neste período."
        )
    pages[-1] += "\n\nLegenda\n" + LEGEND + ("\n⚪ - Local" if has_local else "")
    for i, page in enumerate(pages[1:], 1):
        stage(db, turn.chat_id, f"agenda:{turn.id}:{i}", page, now)
    return pages[0]


def describe(db, row, verb, destinations=(), *, details=()):
    if destinations:
        pending = "Aguardando confirmação de " + " e ".join(destinations) + "."
    elif row.source_id:
        pending = "Alteração aguardando sincronização com " + (
            "Google e iCloud."
            if row.pair_id
            else PROVIDERS[db.get_one(CalendarSource, row.source_id).provider] + "."
        )
    elif row.kind == "event":
        pending = "Cancelado na agenda local." if row.deleted else "Salvo na agenda local."
    else:
        pending = ""
    collisions = service.conflicts(db, row)
    warning = (
        "Conflito de horário com " + ", ".join(t[:100] for t in collisions[:4]) + "."
        if collisions
        else ""
    )
    when = row.starts_at.astimezone(ZONE)
    label = when.strftime("%d/%m/%Y · dia inteiro" if row.all_day else "%d/%m/%Y às %H:%M")
    lines = [f"{verb} · {row.title[:300]}", f"{label} (Manaus) · #{str(row.id)[:8]}", *details]
    if pending:
        lines.append(pending)
    if warning:
        lines.append(warning)
    return "\n".join(lines)


def _time_marker(normal: str, match: re.Match[str]) -> bool:
    # “as contas” e “para as contas” podem fazer parte do assunto.
    if match[0].strip() in {"as", "para as", "pras"}:
        return CLOCK.match(normal, match.start()) is not None
    return True


def _schedule_fields(tail: str, received: datetime) -> tuple[str, datetime]:
    normal = fold(tail)
    marker = next((m for m in WHEN.finditer(normal) if _time_marker(normal, m)), None)
    if marker is None:
        raise Problem(
            422,
            "missing_time",
            "Informe o assunto e o horário. Exemplo: me lembre de pagar a conta amanhã às 9.",
        )

    before = tidy(tail[: marker.start()])
    before = tidy(re.sub(r"(?:^|\s)(?:para(?: o)?|pra|no|na|em)$", "", before, flags=re.I))
    phrase = tail[marker.start() :]
    if before:
        return before, delivery_time(phrase, received).instant

    due, end = time_prefix(phrase, received)
    remainder = phrase[end:]
    # Um segundo horário ou uma alternativa exige nova instrução. Não a transforme em assunto.
    rest = fold(remainder).lstrip(" ,")
    next_marker = WHEN.match(rest)
    alternative = re.match(r"(?:e|ou) (.+)", rest)
    if (
        (next_marker and _time_marker(rest, next_marker))
        or (
            alternative and (WHEN.match(alternative[1]) or re.match(rf"{NUMBER}\b", alternative[1]))
        )
        or re.match(r"[/+\d]|[-.:]\d", rest)
    ):
        raise ValueError
    title = tidy(re.sub(r"^(?:(?:de|para)(?: que)?|que)\s+", "", tidy(remainder), flags=re.I))
    if not title:
        raise Problem(
            422, "missing_title", "Qual é o assunto? Repita o comando com assunto e horário."
        )
    return title, due.instant


def clean_subject(subject: str):
    return re.sub(
        r"^(?:haver[áa]|vai haver|vai ter|terei|teremos|eu tenho|vou ter)\s+",
        "",
        subject.lstrip(" ,;:.-–—"),
        flags=re.I,
    ).strip()


def _event_fields(tail, received):
    # Remove a indicação de destino, inclusive quando o ditado a coloca depois do título.
    tail = re.sub(
        r"\b(?:n[ao]|para [ao]) (?:minha |meu )?(?:agenda|calend[áa]rio)\b", "", tail, flags=re.I
    )
    tail = tidy(" ".join(tail.split()))
    normal = fold(tail)
    numbered_month = re.search(
        rf"\bdia ({DAY_NUMBER}) (?:deste mes|desse mes|do (?:mes (?:que vem|passado|seguinte)|proximo mes))\b",
        normal,
    )
    if numbered_month:
        anchor = month_anchor(received.astimezone(ZONE).date(), numbered_month[0])
        day = anchor.replace(day=day_number(numbered_month[1]))
        return _event_fields(
            tail[: numbered_month.start()] + day.isoformat() + tail[numbered_month.end() :],
            received,
        )
    marker = next((m for m in WHEN.finditer(normal) if _time_marker(normal, m)), None)
    if marker:
        phrase = normal[marker.start() :]
        if (relative := DURATION.match(phrase)) and relative["unit"].startswith("dia"):
            day = delivery_time(phrase[: relative.end()], received).instant.date()
            normalized = (
                tail[: marker.start()] + day.isoformat() + tail[marker.start() + relative.end() :]
            )
            return _event_fields(normalized, received)
        day, used = read_day(phrase, received.astimezone(ZONE).date())
        rest = tidy(phrase[used:])
        before = tidy(
            re.sub(
                r"(?:^|\s)(?:para(?: o)?|pra|no|na|em)$",
                "",
                tidy(tail[: marker.start()]),
                flags=re.I,
            )
        )
        if used and (
            not rest
            or (
                not before
                and not re.match(
                    r"(?:as|ou|e|por volta|pela|de manha|de tarde|de noite|\d+)\b", rest
                )
                and not CLOCK.match(rest)
                and not WHEN.search(rest)
            )
        ):
            title = before or clean_subject(tail[marker.start() + used :])
            if not title:
                raise Problem(422, "missing_title", "Qual é o assunto do compromisso?")
            return title, datetime.combine(day, time.min, ZONE), True
    title, when = _schedule_fields(tail, received)
    return clean_subject(title), when, False


def parse_event_input(tail: str, received: datetime, alert: int | None = None):
    # Se o usuário ditou as duas horas, substitui o intervalo pelo início para
    # reaproveitar a mesma interpretação da data e do assunto.
    for marker in re.finditer(r"\bdas ", fold(tail)):
        normalized = fold(tail)
        first = CLOCK.match(normalized, marker.end())
        if not first:
            continue
        separator = re.match(r"\s+(?:ate(?: as)?|as)\s+", normalized[first.end() :])
        if not separator:
            raise ValueError
        last = CLOCK.match(normalized, first.end() + separator.end())
        if not last:
            raise ValueError
        start_text = first[0]
        if last["period"] and not first["period"] and not first["noon"]:
            start_text += " da " + last["period"]
        single = tail[: marker.start()] + "às " + start_text + tail[last.end() :]
        title, start, _ = _event_fields(single, received)
        end = delivery_time(f"{start:%Y-%m-%d} às {last[0]}", received).instant
        if end <= start:
            raise Problem(
                422,
                "time_range",
                "O fim precisa vir depois do início. Confira os horários do compromisso.",
            )
        return EventInput(title=title, starts_at=start, ends_at=end, alert_minutes=alert)
    title, start, all_day = _event_fields(tail, received)
    return EventInput(title=title, starts_at=start, all_day=all_day, alert_minutes=alert)


def acknowledge(db, recorded, route):
    row, data = recorded.row, recorded.data
    when = data.starts_at.astimezone(ZONE)
    db.merge(CalendarFocus(route=route, anchor=when.date(), period="day"))
    if data.kind == "reminder":
        verb = "Lembrete salvo"
    elif data.all_day or data.ends_at:
        verb = "Compromisso registrado"
    else:
        verb = "Compromisso de uma hora registrado"
    details = []
    if data.ends_at:
        details.append(f"Término às {data.ends_at.astimezone(ZONE):%H:%M}.")
    if data.alert_minutes is not None:
        details.append(f"Aviso {data.alert_minutes} minutos antes.")
    return describe(db, row, verb, recorded.providers, details=details)


def personal(db: Session, turn: Turn, now: datetime, *, unavailable=(), google_linked=False):
    raw = command_text(turn.text)
    normal = fold(raw)
    try:
        if normal in ("quando e meu aniversario", "qual a data do meu aniversario"):
            birthday = service.routine(db).birthday
            return (
                "Seu aniversário é em " + "/".join(reversed(birthday.split("-"))) + "."
                if birthday
                else "Qual é o dia e o mês do seu aniversário?"
            )
        resumed = destinations.resume(db, turn.chat_id, normal, now)
        if resumed is not None:
            return resumed if isinstance(resumed, str) else acknowledge(db, resumed, turn.chat_id)
        if is_query(turn.text):
            return listing(db, turn, now, normal, unavailable)
        if normal.startswith("meu aniversario"):
            hit = re.fullmatch(
                r"meu aniversario (?:e |sera )?(?:no )?(?:dia )?(\d{1,2})(?:/| de )([a-z]+|\d{1,2})",
                normal,
            )
            if not hit:
                return "Informe o aniversário como dia/mês. Exemplo: meu aniversário é 14/03."
            month = int(hit[2]) if hit[2].isdigit() else MONTHS.index(hit[2]) + 1
            birthday = date(2000, month, int(hit[1])).strftime("%m-%d")
            prefs = RoutineInput.model_validate(service.routine(db), from_attributes=True)
            service.set_routine(db, prefs.model_copy(update={"birthday": birthday}))
            return f"Anotei seu aniversário em {int(hit[1]):02d}/{month:02d}."
        if change := CHANGE.fullmatch(normal):
            deleting = change[1] in ("cancele", "cancela", "desmarque")
            if (deleting and change[4]) or QUALIFIED.search(fold(turn.text)):
                return CLARIFY_ACTION
            rows = [
                r
                for r in db.scalars(select(Entry).where(Entry.deleted.is_(False)))
                if str(r.id).startswith(change[3])
            ]
            if len(rows) != 1:
                return "Não identifiquei o item. Consulte a agenda e confira o código."
            row = rows[0]
            if row.rules:
                return "Este compromisso se repete. No painel, escolha a ocorrência ou a série para confirmar a alteração."
            when = row.starts_at
            if not deleting:
                when = delivery_time(
                    re.sub(r"^para (?!as )", "", change[4] or ""), turn.received_at or now
                ).instant
                if when <= now:
                    return "Escolha um horário futuro para remarcar."
            with db.begin_nested():
                copies = service.change_targets(db, row.id, row.version)
                row = copies[0]
                if deleting:
                    for copy in copies:
                        service.remove(db, copy)
                    return describe(db, row, "Cancelamento registrado")
                duration = row.ends_at - row.starts_at
                data = EventInput(
                    kind="reminder" if row.kind == "reminder" else "event",
                    title=row.title,
                    starts_at=when,
                    ends_at=when + (timedelta(hours=1) if row.all_day else duration),
                    all_day=False,
                    alert_minutes=row.alert_minutes,
                )
                for copy in copies:
                    service.write(db, data, now=now, existing=copy)
                return describe(db, row, "Remarcado")
        if later := postposed_event(normal):
            if "?" in turn.text or REPORTED.search(normal) or re.search(r"\bnao\b", normal):
                return CLARIFY_ACTION
            target = raw[later.start("target") :]
            scope = request_scope(target)
            if scope.problem:
                return scope.problem
            if scope.text and not re.fullmatch(
                r"(?:n[ao]|em) (?:minha |meu )?(?:agenda|calendario)", fold(scope.text)
            ):
                return CLARIFY_ACTION
            subject = re.sub(r"^(?:eu )?tenho\s+", "", raw[: later.end("body")], flags=re.I)
            raw = f"Marque {subject} {target}"
            normal = fold(raw)
        hit = CREATE.match(normal)
        if not hit:
            return None
        scope = request_scope(raw)
        if scope.problem:
            return scope.problem
        if QUALIFIED.search(fold(turn.text)):
            return CLARIFY_ACTION
        if re.search(r"\bou\b", fold(scope.text)):
            return "Há mais de uma opção no pedido. Confirme a data ou o horário e o assunto do compromisso."
        if (
            re.match(
                r"^(?:anote|registr[ae]|registrar|crie|criar|cadastre|cadastrar|bote|botar|ponha|poe|salve|salvar|coloque|colocar|insira|inserir|inclua|incluir) ",
                normal,
            )
            and not hit["rem"]
            and not scope.providers
            and not re.search(r"\b(?:agendas?|calendarios?|compromissos?|eventos?)\b", normal)
        ):
            return None
        if not hit["rem"] and normal.startswith(("agende ", "agenda ")):
            target = re.match(r"^(?:agende|agenda) (?:para|pra|pro) (.+)", normal)
            if target:
                # “para amanhã” pode abrir a data; “para Ana” identifica um destinatário.
                if not WHEN.match(target[1]) or split_fields(target[1])[0]:
                    return None
            elif re.search(r" (?:que|dizendo) ", normal):
                return None
        if hit["rem"]:
            tail = tidy(raw[hit.end() :])
        else:
            # O destino pode vir antes ou depois da data e do assunto.
            clean = scope.text
            verb = CREATE.match(fold(clean))
            tail = tidy(clean[verb.end() :] if verb else "")
        alert = None
        if not hit["rem"]:
            aviso = re.search(
                r"[, ]+(?:e )?(?:me lembre|me avise|com (?:um )?aviso)(?: com)? (\d{1,4}) minutos? (?:antes|de antecedencia)$",
                fold(tail),
            )
            if aviso:
                alert = int(aviso[1])
                tail = tidy(tail[: aviso.start()])
        received = turn.received_at or now
        if hit["rem"]:
            title, when = _schedule_fields(tail, received)
            data = EventInput(kind="reminder", title=title, starts_at=when)
        else:
            data = parse_event_input(tail, received, alert)
        when = data.starts_at
        if when <= now and (not data.all_day or when.date() < now.astimezone(ZONE).date()):
            return "Esse horário já passou. Informe um horário futuro."
        recorded = destinations.start(db, data, turn.chat_id, now, scope.providers, google_linked)
        return recorded if isinstance(recorded, str) else acknowledge(db, recorded, turn.chat_id)
    except Problem as err:
        return err.message
    except ConflictingDates:
        return "As datas informadas são diferentes. Qual dia devo usar? Repita o pedido com uma única data."
    except (ValueError, OverflowError, ValidationError):
        return "Não consegui identificar a data ou o horário. Repita com um dia e uma hora, por exemplo: amanhã às 10h30."
