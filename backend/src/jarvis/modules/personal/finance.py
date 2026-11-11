"""Livro-caixa em centavos. Um orçamento mensal é diferente de saldo bancário."""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select

from jarvis.modules.assistant.dates import CALENDAR_DATE, MONTH_NAME, read_day
from jarvis.modules.assistant.intent import fold
from jarvis.modules.calendar.periods import requested_period
from jarvis.modules.calendar.service import bounds
from jarvis.modules.errors import Problem
from jarvis.modules.identity.preferences import Preferences
from jarvis.modules.personal.models import LedgerEntry
from jarvis.modules.personal.money import VALUE, FinanceInput, amount, money
from jarvis.modules.spreadsheets.service import remove_ledger, reserve_cents
from jarvis.modules.stickers.policy import DISTRESS

RECORD = re.compile(
    r"^(?:(?:eu )?(?:ja )?(?P<expense>gastei|paguei|desembolsei|comprei)|(?:eu )?(?P<income>recebi|ganhei)|(?:(?:anote|registre|lance|salve|adicione) (?:que (?:eu )?)?)(?:(?P<earned>recebi|ganhei|(?:uma? )?(?:receita|entrada))|(?:gastei|paguei|(?:uma? )?(?:despesa|gasto))))\b"
)
BUDGET = re.compile(
    r"^(?:(?:meu (?:orcamento|limite de gastos?)|(?:defina|definir|ajuste|fixe|coloque) (?:o |meu |um )?(?:orcamento|limite(?: de gastos?)?)))\b"
)
ADVICE = re.compile(
    r"^(?:(?:(?:eu )?posso|da (?:pra|para)|consigo|devo) (?:gastar|comprar|pagar)|(?:essa compra|isso) cabe no (?:meu )?orcamento)\b"
)
PROPOSAL = re.compile(
    r"^(?:(?:e )?se eu |(?:eu )?(?:(?:estou|to) pensando em |penso em |(?:vou|pretendo|queria) )?)"
    r"(?P<verb>gastar|gastasse|comprar|comprasse|pagar|pagasse)\b"
)
CURRENCY = re.compile(r"r\$|\b(?:reais|real|centavos?)\b")
NON_MONETARY = re.compile(
    r"\b(?:horas?|minutos?|segundos?|pontos?|seguidores|mensagens?|notificacoes|quilometros?|km|quilos?|litros?)\b"
)
# O próprio dono sinaliza a compra como dispensável. O produto não define isso.
OPTIONAL = re.compile(
    r"\b(?:desnecessari[oa]s?|superflu[oa]s?|por impulso|sem (?:precisar|necessidade)|"
    r"(?:nem|nao) (?:preciso|precisava|precisaria|(?:e|era|foi) necessari[oa])|"
    r"nao (?:devia|deveria) ter (?:gasto|comprado|feito))\b"
)
ESSENTIAL = re.compile(
    r"\b(?:necessari[oa]s?|essencia(?:l|is)|indispensave(?:l|is)|urgente|precis(?:o|ava|ei)|"
    r"aluguel|moradia|alimentacao|comida|supermercado|mercado|farmacia|remedios?|medicamentos?|"
    r"consulta medica|saude|ambulancia|fraldas?|cesta basica|conta de (?:agua|luz|energia))\b"
)
REPORT = re.compile(
    r"^(?:quanto (?:eu )?(?:ja |ainda |ainda posso )?(?:gastei|recebi|ganhei|sobra|sobrou|resta|posso gastar)|(?:me )?(?:mostre|mostra|liste|lista|resuma|qual(?: e| foi)?|quais(?: sao| foram)?) (?:o |a |os |as |meu |minha |meus |minhas |os meus |as minhas )*(?:gastos|despesas|receitas|entradas|saldo|resumo financeiro|orcamento|limite de gastos)|(?:meus? |minhas? )?(?:gastos|despesas|receitas|saldo|resumo financeiro|extrato financeiro|orcamento))\b"
)
REMOVE = re.compile(
    r"^(?:apague|remova|exclua|cancele) (?:o |a )?(?:gasto|despesa|receita|lancamento|orcamento)\s+#?f(\d{1,15})$"
)
DATE = re.compile(
    rf"\b(?:(?:no |em )?(?:dia )?(?:{CALENDAR_DATE})|(?:no )?dia \d{{1,2}}|anteontem|ontem|hoje|amanha)\b"
)
SCOPE = re.compile(
    rf"\b(?:(?:n[oa]|de|em|d[oa]|deste|desse|neste|nesse)\s+)?(?:este |esta |essa |esse |proxim[oa] |ultim[oa] )?(?:mes|semana|ano)(?: (?:passad[oa]|anterior|que vem|atual))?\b|\b(?:de |em )?(?:{MONTH_NAME})(?: (?:de )?\d{{4}})?\b|\bnos ultimos \d+ dias\b"
)


def dated(raw: str, today: date):
    normal = fold(raw)
    matches = list(DATE.finditer(normal))
    if not matches:
        if re.search(
            r"\b(?:semana|mes|passad[oa]|segunda|terca|quarta|quinta|sexta|sabado|domingo|amanha|depois|dias atras)\b|\d/\d",
            normal,
        ):
            raise FinanceInput("Qual foi a data? Use hoje, ontem ou dia/mês/ano para registrar.")
        return today, raw
    if len(matches) > 1:
        raise FinanceInput("Encontrei mais de uma data. Informe a data deste lançamento.")
    hit = matches[0]
    value = re.sub(r"^(?:no |em )", "", hit[0])
    offsets = {"ontem": -1, "anteontem": -2, "hoje": 0, "amanha": 1}
    try:
        day = (
            today + timedelta(days=offsets[value])
            if value in offsets
            else read_day(value, today)[0]
        )
    except (ValueError, OverflowError) as err:
        raise FinanceInput("Essa data não é válida.") from err
    if re.match(
        r"[/\d-]|\s+(?:do proximo mes|do mes passado|da proxima semana)", normal[hit.end() :]
    ):
        raise FinanceInput("Informe a data completa no formato dia/mês/ano.")
    return day, raw[: hit.start()] + " " + raw[hit.end() :]


def period(text: str, today: date):
    if "desde sempre" in text or "todo o historico" in text or "desde o inicio" in text:
        return date.min, today + timedelta(days=1)
    if days := re.search(r"\b(?:nos )?ultimos (\d{1,4}) dias\b", text):
        value = int(days[1])
        if not 1 <= value <= 3660:
            raise FinanceInput("Informe de 1 a 3660 dias.")
        return today - timedelta(days=value - 1), today + timedelta(days=1)
    span = re.search(rf"\b(?:de|entre) ({CALENDAR_DATE}) (?:a|ate|e) ({CALENDAR_DATE})\b", text)
    if span:
        start = read_day(span[1], today)[0]
        end = read_day(span[2], today)[0]
        if end < start:
            raise FinanceInput("A data final precisa vir depois da inicial.")
        return start, end + timedelta(days=1)
    if re.search(r"\bano\b", text) or re.fullmatch(r"\s*(?:(?:em|de) )?\d{4}\s*", text):
        year = re.search(r"\b(\d{4})\b", text)
        y = int(year[1]) if year else today.year - int("passado" in text or "anterior" in text)
        return date(y, 1, 1), date(y + 1, 1, 1)
    if not (
        DATE.search(text)
        or SCOPE.search(text)
        or re.search(r"\b(dia|ontem|semana|mes|periodo|desde|entre|dias|ultim[oa]s)\b", text)
    ):
        return today.replace(day=1), bounds(today, "month")[1].date()
    try:
        anchor, unit = requested_period(text, today)
    except (Problem, ValueError, OverflowError) as err:
        raise FinanceInput(
            "Qual período devo somar? Pode ser hoje, esta semana, mês passado ou um mês e ano."
        ) from err
    start, end = bounds(anchor, unit)
    return start.date(), end.date()


def purchase_period(raw: str, today: date):
    """Uma simulação usa o orçamento da data ou do mês que o dono indicou."""
    normal = fold(raw)
    if DATE.search(normal) or not SCOPE.search(normal):
        day, remaining = dated(raw, today)
        if not SCOPE.search(fold(remaining)):
            return day, remaining
    else:
        scopes = list(SCOPE.finditer(normal))
        if len(scopes) == 1:
            scope = scopes[0]
            start, end = period(scope[0], today)
            if start.day == 1 and end == bounds(start, "month")[1].date():
                return start, raw[: scope.start()] + " " + raw[scope.end() :]
    raise FinanceInput("Para qual mês quer avaliar essa compra?")


def ledger_rows(route: str, start: date, end: date):
    return (LedgerEntry.route == route, LedgerEntry.day >= start, LedgerEntry.day < end)


def monthly_budget(db, route, month):
    month = month.replace(day=1)
    common = (LedgerEntry.route == route,)
    custom = db.scalar(
        select(LedgerEntry)
        .where(*common, LedgerEntry.kind == "budget", LedgerEntry.day == month)
        .order_by(LedgerEntry.inbound_id.desc())
        .limit(1)
    )
    if custom:
        return custom
    return db.scalar(
        select(LedgerEntry)
        .where(*common, LedgerEntry.kind == "monthly", LedgerEntry.day <= month)
        .order_by(LedgerEntry.day.desc(), LedgerEntry.inbound_id.desc())
        .limit(1)
    )


@dataclass(frozen=True)
class MonthlyMargin:
    limit: int
    spent: int
    reserve: int

    @property
    def available(self):
        return self.limit - self.spent - self.reserve


def monthly_margin(db, route, month):
    limit = monthly_budget(db, route, month)
    if not limit:
        return None
    start, end = bounds(month, "month")
    spent = int(
        db.scalar(
            select(func.sum(LedgerEntry.amount)).where(
                *ledger_rows(route, start.date(), end.date()), LedgerEntry.kind == "expense"
            )
        )
        or 0
    )
    return MonthlyMargin(limit.amount, spent, reserve_cents(db, route, month))


def needs_sober_reply(context: str):
    return bool(
        DISTRESS.search(context)
        or re.search(
            r"\bnao (?:e|era|foi|parece) (?:desnecessari[oa]|superflu[oa]|por impulso)\b", context
        )
        or ESSENTIAL.search(OPTIONAL.sub("", context))
    )


def budget_answer(
    db, route, month, purchase=None, *, context="", profile: Preferences | None = None
):
    margin = monthly_margin(db, route, month)
    sober = needs_sober_reply(context)
    optional = bool(OPTIONAL.search(context) and not sober)
    casual = profile is not None and profile.casual and not sober
    if margin is None:
        if purchase is not None and optional:
            lead = "Ei pô, te acalma. " if casual else ""
            return f"{lead}Eu esperaria antes de gastar esses {money(purchase)}. Ainda não tenho um limite de gastos para comparar com essa compra."
        return "Ainda não tenho um limite de gastos para esse mês. Diga, por exemplo, «meu orçamento mensal é de R$ 1.500»."
    available = margin.available
    details = [
        f"Orçamento de {month:%m/%Y} · {money(margin.limit)}",
        f"Gastos registrados · {money(margin.spent)}",
    ]
    if margin.reserve:
        details.append(f"Reserva protegida: {money(margin.reserve)}")
    if purchase is None:
        if available >= 0:
            answer = f"Você ainda tem {money(available)} de margem no orçamento."
        else:
            answer = f"Você já passou do valor disponível para gastos em {money(-available)}."
    else:
        diff = available - purchase
        if diff >= 0:
            if optional:
                lead = "Ei pô, te acalma. " if casual else ""
                answer = f"{lead}Eu esperaria antes de gastar esses {money(purchase)}. A compra deixaria {money(diff)} de margem."
            else:
                answer = f"Cabe no seu orçamento. A compra de {money(purchase)} deixaria {money(diff)} de margem."
        else:
            over_limit = margin.spent + purchase - margin.limit
            if casual and profile:
                lead = (
                    "Tá ficando doido? "
                    if over_limit > 0 and profile.uses_masculine_endearments
                    else "Ei pô, te acalma. "
                )
            else:
                lead = ""
            intro = (
                f"Com essa compra de {money(purchase)}, "
                if sober
                else f"Eu adiaria essa compra de {money(purchase)}. Com ela, "
            )
            if over_limit > 0:
                answer = f"{lead}{intro}você ficaria {money(over_limit)} acima do limite do mês."
                if margin.reserve:
                    answer += f" Ao todo, faltariam {money(-diff)} para manter a reserva."
            else:
                answer = f"{lead}{intro}faltariam {money(-diff)} para manter a reserva."
        details.append(f"Margem antes da compra · {money(available)}")
    return answer + "\n\n" + "\n".join(details)


def expense_confirmation(db, entry: LedgerEntry, confirmation: str, *, context: str, casual: bool):
    margin = monthly_margin(db, entry.route, entry.day)
    informal = casual and not needs_sober_reply(f"{context} {entry.searchable}")
    optional = informal and bool(OPTIONAL.search(context))
    if margin is None:
        if optional:
            return f"Ei pô, te controla. Ainda não tenho um limite para comparar com essa despesa.\n\n{confirmation}"
        return confirmation
    if margin.available >= 0:
        if not optional:
            return confirmation
        result = f"Ainda sobram {money(margin.available)} de margem em {entry.day:%m/%Y}"
        if margin.reserve:
            result += f", mantendo a reserva de {money(margin.reserve)}"
        result += "."
    elif margin.reserve:
        result = f"Pelos gastos registrados em {entry.day:%m/%Y}, faltam {money(-margin.available)} para manter a reserva de {money(margin.reserve)}."
    else:
        result = f"Os gastos de {entry.day:%m/%Y} ficaram {money(-margin.available)} acima do orçamento de {money(margin.limit)}."
    lead = "Ei pô, te controla. " if informal else ""
    return f"{lead}{result}\n\n{confirmation}"


def finance(db, turn, raw, normal, today, *, question, context, profile: Preferences):
    try:
        if remove := REMOVE.fullmatch(normal):
            try:
                remove_ledger(db, turn.chat_id, int(remove[1]))
            except Problem as exc:
                return exc.message
            return f"Excluí o lançamento F{remove[1]}."
        advise = ADVICE.match(normal)
        if not advise and (plan := PROPOSAL.match(normal)):
            # “Quero comprar dois livros” não diz o preço. “Quero gastar 20 minutos” também não.
            tail = normal[plan.end() :]
            if (
                CURRENCY.search(tail)
                or OPTIONAL.search(tail)
                or re.search(r"\b(?:orcamento|limite de gastos)\b", tail)
                or re.search(r"\bpor\s+\d", tail)
                or (
                    plan["verb"].startswith("gast")
                    and VALUE.search(tail)
                    and not NON_MONETARY.search(tail)
                )
            ):
                advise = plan
        budget = BUDGET.match(normal)
        if (
            budget
            and not question
            and VALUE.search(normal)
            and not re.search(r"\b(?:nao|era|talvez|seria|fosse|poderia|estimo)\b", normal)
        ):
            clean = normal[budget.end() :]
            month = today.replace(day=1)
            if scopes := list(SCOPE.finditer(clean)):
                start, end = period(" ".join(m[0] for m in scopes), today)
                if start.day != 1 or end != bounds(start, "month")[1].date():
                    raise FinanceInput("O orçamento é mensal. Informe um mês e um valor.")
                month = start
            value, _ = amount(SCOPE.sub(" ", clean))
            kind = "monthly" if re.search(r"\bmensal\b|todo mes", normal) else "budget"
            db.add(
                LedgerEntry(
                    inbound_id=turn.id,
                    route=turn.chat_id,
                    kind=kind,
                    day=month,
                    amount=value,
                    description="Orçamento",
                    searchable="orcamento",
                )
            )
            return f"Orçamento {'mensal a partir de' if kind == 'monthly' else 'de'} {month:%m/%Y}: {money(value)} (F{turn.id})."
        if advise:
            day, clean = purchase_period(raw[advise.end() :], today)
            remaining = fold(clean)
            if not VALUE.search(remaining):
                if "quanto" in normal:
                    return budget_answer(db, turn.chat_id, day)
                return "Qual é o valor da compra em reais?"
            if (
                re.search(r"\b(?:comprar|comprasse)\b", advise[0])
                and not CURRENCY.search(remaining)
                and not re.search(r"\b(?:por|custa|custando)\s+\d", remaining)
            ):
                return "Qual é o preço da compra em reais?"
            value, _ = amount(remaining)
            return budget_answer(db, turn.chat_id, day, value, context=context, profile=profile)
        if record := RECORD.match(normal):
            if (
                question
                or "?" in raw
                or re.search(
                    r"\b(?:se|caso|acho que|mais ou menos|cerca de|nao|talvez|(?:quero|vou|pretendo|pensando em) (?:gastar|comprar|pagar))\b",
                    OPTIONAL.sub("", normal),
                )
            ):
                return None
            posted = fold(raw[record.end() :])
            currency = bool(CURRENCY.search(posted))
            if NON_MONETARY.search(posted) and not currency:
                return None
            if (
                record["expense"] == "comprei"
                and not currency
                and not re.search(r"\bpor\b", posted)
            ):
                return None
            if (
                record["income"]
                and not currency
                and not re.search(
                    r"\b(?:salario|comissao|pix|pagamento|deposito|transferencia|bonus|renda|cliente)\b",
                    posted,
                )
            ):
                if not re.fullmatch(r"\s*\d+(?:[,.]\d+)*(?: hoje| ontem)?", posted):
                    return None
            day, clean = dated(raw[record.end() :], today)
            if day > today:
                raise FinanceInput(
                    "Para o livro-caixa, informe uma despesa ou receita já realizada."
                )
            value, hit = amount(fold(clean))
            description = " ".join((clean[: hit.start()] + " " + clean[hit.end() :]).split()).strip(
                " ,.;:-"
            )
            description = re.sub(
                r"^(?:(?:de|no|na|em|com|por|um|uma|para)\s+)+", "", description, flags=re.I
            )
            description = re.sub(r"\s+(?:de|por|com|no|na|em)$", "", description, flags=re.I).strip(
                " ,.;:-"
            )
            if len(description) > 240:
                raise FinanceInput("Use uma descrição de até 240 caracteres.")
            kind = "income" if record["income"] or record["earned"] else "expense"
            entry = LedgerEntry(
                inbound_id=turn.id,
                route=turn.chat_id,
                kind=kind,
                amount=value,
                description=description or "Sem descrição",
                searchable=fold(description),
                day=day,
            )
            db.add(entry)
            label = "Receita" if kind == "income" else "Despesa"
            receipt = f"{label} registrada em {day:%d/%m/%Y}: {money(value)} · {entry.description} (F{turn.id})."
            if kind == "income":
                return receipt
            return expense_confirmation(db, entry, receipt, context=context, casual=profile.casual)
        report = REPORT.match(normal)
        if not report and not budget:
            return None
        prefix = report or budget
        assert prefix is not None
        tail = normal[prefix.end() :]
        start, end = period(tail, today)
        if budget or re.search(r"\b(orcamento|resta|sobra|sobrou|posso gastar|limite)\b", normal):
            if end != start + timedelta(days=1) and (
                end != bounds(start, "month")[1].date() or start.day != 1
            ):
                raise FinanceInput("O limite é mensal. Para qual mês quer consultar o orçamento?")
            return budget_answer(db, turn.chat_id, start)
        filters = ledger_rows(turn.chat_id, start, end)
        category = SCOPE.sub(" ", DATE.sub(" ", tail))
        selected = re.fullmatch(
            r"\s*(?:com |no |na |em |de )(?:o |a )?([a-z][a-z ]*?)\s*", category
        )
        if selected and not re.search(
            r"\b(?:historico|inicio|sempre|ano|semana|mes|dia)\b", selected[1]
        ):
            filters += (LedgerEntry.searchable.contains(selected[1].strip(), autoescape=True),)
        both = bool(re.search(r"\b(saldo|resumo|extrato)\b", normal))
        kind = (
            "income" if re.search(r"\b(receitas|entradas|recebi|ganhei)\b", normal) else "expense"
        )
        kinds = ["expense", "income"] if both else [kind]
        totals = dict(
            db.execute(
                select(LedgerEntry.kind, func.sum(LedgerEntry.amount))
                .where(*filters, LedgerEntry.kind.in_(kinds))
                .group_by(LedgerEntry.kind)
            ).all()
        )
        expense, income = int(totals.get("expense", 0)), int(totals.get("income", 0))
        last = end - timedelta(days=1)
        if start == date.min:
            label = f"até {last:%d/%m/%Y}"
        elif start == last:
            label = f"de {start:%d/%m/%Y}"
        else:
            label = f"de {start:%d/%m/%Y} a {last:%d/%m/%Y}"
        if both:
            return (
                f"Resumo {label}\n\n"
                f"Entradas registradas · {money(income)}\n"
                f"Gastos registrados · {money(expense)}\n"
                f"Saldo dos lançamentos: {money(income - expense)}"
            )
        total = income if kind == "income" else expense
        result = f"{'Receitas' if kind == 'income' else 'Gastos'} {label}: {money(total)}."
        if not normal.startswith("quanto") and total:
            rows = db.scalars(
                select(LedgerEntry)
                .where(*filters, LedgerEntry.kind == kind)
                .order_by(LedgerEntry.day.desc(), LedgerEntry.inbound_id.desc())
                .limit(11)
            ).all()
            result += "\n\n" + "\n".join(
                f"{r.day:%d/%m} · {r.description} · {money(r.amount)} · F{r.inbound_id}"
                for r in rows[:10]
            )
            if len(rows) > 10:
                result += "\nMostrei os dez mais recentes. O total inclui todo o período."
        return result
    except FinanceInput as error:
        return str(error)
    except (ValueError, OverflowError):
        return "Confira o valor e a data antes de repetir o pedido."
