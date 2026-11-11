"""Ponto de entrada transacional para memória e finanças pessoais."""

from datetime import datetime

from sqlalchemy.orm import Session

from jarvis.adapters.persistence.models import Owner
from jarvis.modules.assistant.intent import fold
from jarvis.modules.calendar.periods import command_text
from jarvis.modules.calendar.service import ZONE
from jarvis.modules.identity.preferences import Preferences
from jarvis.modules.messaging.models import Inbound, Outbound
from jarvis.modules.messaging.queue import Turn
from jarvis.modules.personal.finance import finance
from jarvis.modules.personal.memory import excluded_history_ids, memory, memory_context
from jarvis.modules.personal.models import PersonalAction
from jarvis.modules.spreadsheets.commands import sheet_command

__all__ = ["excluded_history_ids", "handle_personal", "memory_context"]


def handle_personal(db: Session, turn: Turn, now: datetime) -> str | None:
    if turn.kind not in {"text", "audio"}:
        return None
    owner = db.get(Owner, 1)
    if owner is None:
        return None
    source = db.get(Inbound, turn.id)
    if source is None or source.chat_id != turn.chat_id:
        return None
    # A fila reserva uma só transação por entrada. O recibo também impede uma
    # segunda mutação caso o mesmo comando seja invocado novamente pelo roteador.
    if db.get(PersonalAction, turn.id):
        previous = db.get(Outbound, turn.id)
        return previous.text if previous else "Esse pedido já foi processado."
    raw = command_text(turn.text)
    normal = fold(raw)
    question = turn.text.rstrip().endswith("?")
    today = (turn.received_at or now).astimezone(ZONE).date()
    result = sheet_command(db, turn, raw, normal, today, question=question)
    if result is None:
        result = finance(
            db,
            turn,
            raw,
            normal,
            today,
            question=question,
            context=fold(source.text),
            profile=Preferences.for_owner(owner),
        )
    if result is None:
        result = memory(db, turn, raw, normal, question=question)
    if result is not None:
        db.add(PersonalAction(inbound_id=turn.id, route=turn.chat_id))
    return result
