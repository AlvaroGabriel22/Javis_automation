"""Comandos explícitos de lançamento e planilha; as gravações usam o serviço transacional."""

import re
from datetime import date

from sqlalchemy.orm import Session

from jarvis.modules.errors import Problem
from jarvis.modules.messaging.queue import Turn
from jarvis.modules.personal.money import VALUE, FinanceInput, amount, money
from jarvis.modules.spreadsheets.service import (
    correct_amount,
    create_note,
    remove_note,
    reserve_cents,
    set_reserve,
)

CORRECT = re.compile(
    r"^(?:corrija|corrige|altere|atualize|ajuste)\s+(?:o\s+valor\s+d[ao]\s+|[oa]\s+)?(?:(?:gasto|receita|despesa|lancamento)\s+)?#?f(\d{1,15})\s+(?:para|pra|em)\s+(.+)$"
)
RESERVE = re.compile(
    r"^(?:(?:defina|ajuste|fixe|registre) (?:a |minha |uma )?reserva(?: mensal| protegida)? (?:em |para |de )?|minha reserva mensal (?:e (?:de )?)?|reserve (?:mensalmente |todo mes ))(.+)$"
)
ZERO = re.compile(r"(?:r\$\s*)?0(?:[.,]0{1,2})?(?: reais?)?|zero(?: reais?)?")
NOTE = re.compile(
    r"^(?:salve|anote|registre|adicione|insira|guarde) (?:isto |isso |o item )?(?:na|em minha) planilha(?: (?:de (?:notas|anotacoes|compras)|geral))?\s*(?:[:,]\s*|\s+|$)"
)
NOTE_REMOVE = re.compile(r"^(?:remova|exclua|apague) (?:a )?(?:anotacao|nota|linha) #?p(\d{1,15})$")
QUANTITY = re.compile(r"(?:;\s*|,\s*|\s+)quantidade\s*[:=]?\s*", re.I)
NOTE_HELP = "Qual é o item? Use «salve na planilha: café; quantidade: 2 pacotes»."


def sheet_command(db: Session, turn: Turn, raw: str, normal: str, today: date, *, question: bool):
    if normal in {
        "qual minha reserva",
        "qual a minha reserva",
        "qual e minha reserva mensal",
        "minha reserva mensal",
    }:
        return (
            f"Reserva protegida em {today:%m/%Y}: {money(reserve_cents(db, turn.chat_id, today))}."
        )
    if question:
        return None
    try:
        if fix := CORRECT.fullmatch(normal):
            if not VALUE.fullmatch(fix[2]):
                return "Qual é o valor correto? Informe o código e um único valor em reais."
            value, _ = amount(fix[2])
            row = correct_amount(db, turn.chat_id, int(fix[1]), value)
            return f"Corrigi o lançamento F{row.inbound_id} para {money(value)}."
        if reserve := RESERVE.fullmatch(normal):
            raw_amount = reserve[1]
            if ZERO.fullmatch(raw_amount):
                value = 0
            elif VALUE.fullmatch(raw_amount):
                value, _ = amount(raw_amount)
            else:
                return "Para a reserva mensal, diga «minha reserva mensal é R$ 200»."
            set_reserve(db, turn.chat_id, today, value)
            return f"Reserva mensal a partir de {today:%m/%Y}: {money(value)}."
        if remove := NOTE_REMOVE.fullmatch(normal):
            remove_note(db, turn.chat_id, int(remove[1]))
            return f"Removi a anotação P{remove[1]}."
        if add := NOTE.match(normal):
            body = raw[add.end() :].strip()
            if not body or normal[add.end() :].startswith(("de ", "do ", "da ")):
                return NOTE_HELP
            fields = QUANTITY.split(body)
            if len(fields) > 2 or not fields[0].strip() or (len(fields) == 2 and not fields[1]):
                return NOTE_HELP
            note = create_note(
                db,
                turn.chat_id,
                today,
                fields[0],
                fields[1] if len(fields) == 2 else "",
                request_key=f"wa:{turn.id}",
            )
            return f"Anotação P{note.id} salva no Jarvis. A atualização da planilha pode ser acompanhada no painel."
    except (Problem, FinanceInput) as exc:
        return str(exc)
    return None
