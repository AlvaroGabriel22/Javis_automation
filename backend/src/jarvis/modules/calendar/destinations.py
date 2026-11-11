"""Seleção transacional das origens e continuação de pedidos por rota WhatsApp."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.models import MessageDraft
from jarvis.modules.assistant.intent import fold, tidy
from jarvis.modules.calendar.models import CalendarChoice, CalendarSource, Entry, ICloudLink
from jarvis.modules.calendar.scope import PROVIDERS
from jarvis.modules.calendar.service import ZONE, EventInput, write
from jarvis.modules.errors import Problem

Options = dict[str, list[dict[str, str]]]


@dataclass
class Recorded:
    data: EventInput
    row: Entry
    providers: list[str]


def options_for(db: Session, requested: frozenset[str], google_linked: bool) -> Options:
    sources = db.scalars(
        select(CalendarSource).order_by(CalendarSource.name, CalendarSource.id)
    ).all()
    linked = {s.provider for s in sources if s.selected or s.available}
    if google_linked:
        linked.add("google")
    if db.get(ICloudLink, 1):
        linked.add("icloud")
    if missing := requested - linked:
        names = " e ".join(PROVIDERS[k] for k in sorted(missing))
        raise Problem(
            409,
            "missing_provider",
            f"Para registrar o compromisso, conecte {names} em Agenda pessoal e repita o pedido.",
        )
    options: Options = {}
    for provider in PROVIDERS:
        if provider not in (requested or linked):
            continue
        selected = [s for s in sources if s.provider == provider and s.selected]
        choices = [s for s in selected if s.chat_target] or [
            s for s in selected if s.available and s.writable
        ]
        if not choices or any(not s.available or not s.writable for s in choices):
            raise Problem(
                409,
                "chat_destination",
                f"Escolha uma agenda de escrita disponível do {PROVIDERS[provider]} em Agenda pessoal → Destinos do WhatsApp e repita o pedido.",
            )
        options[provider] = [{"id": s.id, "name": s.name} for s in choices]
    return options


def pending_provider(options: Options):
    return next((provider for provider, choices in options.items() if len(choices) > 1), None)


def prompt(data: EventInput, provider: str, options: Options):
    choices = options[provider]
    start = data.starts_at.astimezone(ZONE)
    when = start.strftime("%d/%m/%Y · dia inteiro" if data.all_day else "%d/%m/%Y às %H:%M")
    header = f"Em qual agenda do {PROVIDERS[provider]} registro “{data.title}” em {when}?"
    # Reserva espaço para título e instruções mesmo em contas com muitos calendários.
    lines = [f"{i}. {option['name'][:70]}" for i, option in enumerate(choices[:30], 1)]
    if len(choices) > 30:
        lines.append("Há outras agendas. Você também pode responder com o nome completo.")
    return "\n".join(
        [
            header,
            *lines,
            "Responda com o número ou o nome. Para definir a preferência em Destinos do WhatsApp, responda “sempre” e o número. Para desistir, diga “cancelar escolha de agenda”.",
        ]
    )


def record(db: Session, data: EventInput, route: str, now: datetime, options: Options):
    when = data.starts_at.astimezone(ZONE)
    if when <= now and (not data.all_day or when.date() < now.astimezone(ZONE).date()):
        raise Problem(422, "past_event", "Esse horário já passou. Informe um horário futuro.")
    if options:
        copies = [
            write(db, data.model_copy(update={"source_id": choices[0]["id"]}), now=now, route=route)
            for choices in options.values()
        ]
        first = copies[0]
        if len(copies) > 1:
            for copy in copies:
                copy.pair_id = first.id
        first.confirmation_pending = True
    else:
        first = write(db, data.model_copy(update={"source_id": None}), now=now, route=route)
    return Recorded(data, first, [PROVIDERS[p] for p in options])


def start(
    db: Session,
    data: EventInput,
    route: str,
    now: datetime,
    providers: frozenset[str],
    google_linked: bool,
):
    if data.kind == "reminder":
        return Recorded(data, write(db, data, now=now, route=route), [])
    options = options_for(db, providers, google_linked)
    with db.begin_nested():
        if provider := pending_provider(options):
            db.merge(
                CalendarChoice(
                    route=route, event=data.model_dump(mode="json"), options=options, created_at=now
                )
            )
            return prompt(data, provider, options)
        result = record(db, data, route, now, options)
        if prior := db.get(CalendarChoice, route):
            db.delete(prior)
        return result


def resume(db: Session, route: str, text: str, now: datetime):
    draft = db.get(CalendarChoice, route, with_for_update=True)
    if not draft:
        return None
    if text in ("cancelar escolha de agenda", "cancele a escolha de agenda"):
        db.delete(draft)
        return "Cancelei o pedido de compromisso que aguardava a escolha de agenda."
    options = dict(draft.options)
    provider = pending_provider(options)
    if not provider:
        return None
    choices = options[provider]
    data = EventInput.model_validate(draft.event)
    if text.startswith("agenda de "):
        return None
    options_reply = re.fullmatch(
        r"(?:(?:use|usar|escolho|prefiro) )?(?P<always>sempre )?(?:(?:n?[ao] )?(?:agenda |opcao |numero )?)(?P<value>.+)",
        text,
    )
    if not options_reply:
        return None
    value = options_reply["value"].strip('“”"')
    plain = value.removeprefix("#")
    spoken = {
        "um": 1,
        "uma": 1,
        "primeira": 1,
        "dois": 2,
        "duas": 2,
        "segunda": 2,
        "tres": 3,
        "terceira": 3,
        "quatro": 4,
        "quarta": 4,
        "cinco": 5,
        "quinta": 5,
        "seis": 6,
        "sexta": 6,
    }
    number = int(plain) if re.fullmatch(r"\d{1,5}", plain) else spoken.get(plain)
    always = bool(options_reply["always"])
    if number is not None:
        competing = db.get(MessageDraft, route)
        if competing and competing.choices and "agenda " not in text and not always:
            return "Há uma escolha de calendário e outra de contato pendentes. Responda “agenda” e o número para o compromisso, ou o telefone completo para escolher o contato do recado."
        selected = choices[number - 1 : number] if number > 0 else []
    else:
        selected = [option for option in choices if fold(tidy(option["name"])) in (value, text)]
        if not selected:
            if always or text.startswith(("escolho ", "use a agenda ", "prefiro a agenda ")):
                return prompt(data, provider, options)
            return None
    if now - draft.created_at > timedelta(days=1):
        db.delete(draft)
        return "A escolha de agenda expirou. Repita o pedido com o dia e o assunto."
    if len(selected) != 1:
        return prompt(data, provider, options)
    target = db.get(CalendarSource, selected[0]["id"], with_for_update=True)
    if not target or not target.available or not target.writable or not target.selected:
        db.delete(draft)
        return (
            "Essa agenda mudou ou perdeu permissão. Confira as agendas no painel e repita o pedido."
        )
    options[provider] = selected
    try:
        with db.begin_nested():
            if always:
                candidates = db.scalars(
                    select(CalendarSource)
                    .where(CalendarSource.provider == provider)
                    .with_for_update()
                )
                for source in candidates:
                    source.chat_target = source.id == target.id
            if other := pending_provider(options):
                draft.options = options
                return prompt(data, other, options)
            result = record(db, data, route, now, options)
    except Problem as exc:
        db.delete(draft)
        return exc.message
    db.delete(draft)
    return result
