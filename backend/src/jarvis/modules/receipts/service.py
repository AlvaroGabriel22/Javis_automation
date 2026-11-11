"""Confere os campos da foto e reserva a escrita financeira para a confirmação do dono."""

import hashlib
import re
import unicodedata
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.models import Owner
from jarvis.modules.assistant.intent import fold
from jarvis.modules.calendar.service import ZONE
from jarvis.modules.errors import Problem
from jarvis.modules.identity.preferences import Preferences
from jarvis.modules.messaging.models import Inbound
from jarvis.modules.messaging.queue import Turn
from jarvis.modules.personal.finance import expense_confirmation
from jarvis.modules.personal.models import LedgerEntry
from jarvis.modules.personal.money import VALUE, FinanceInput, amount, money
from jarvis.modules.receipts.models import ReceiptDraft
from jarvis.modules.receipts.reading import ReceiptReading, paid_amount, read_date
from jarvis.modules.spreadsheets.service import record_expense

DECISION = re.compile(
    r"^(?P<action>confirmar|confirmo|confirme|cancelar|cancele|reabrir|reabra|valor|data|descricao) "
    r"(?:(?:do |o )?cupom )?#?c(?P<id>[1-9]\d{0,9})(?:\s+(?P<field>[^?!\n]+))?[.!]?$"
)


def _description(value: str | None):
    # Campos impressos são rótulos, mesmo quando parecem um comando.
    if not value:
        return "Compra do cupom"
    return (
        " ".join(
            "".join(
                c if not unicodedata.category(c).startswith("C") else " " for c in value
            ).split()
        )[:180]
        or "Compra do cupom"
    )


def _show(db: Session, row: ReceiptDraft):
    ref = f"C{row.image_id}"
    if row.state == "confirmed":
        entry = db.get(LedgerEntry, row.image_id)
        if entry is None or entry.route != row.route:
            return f"A despesa F{row.image_id} deste cupom foi excluída. A exclusão foi preservada."
        return f"O cupom {ref} já foi registrado como F{row.image_id}. Valor atual: {money(entry.amount)}."
    if row.state == "cancelled":
        return f"O cupom {ref} está cancelado. Para retomar, diga “reabrir cupom {ref}”."
    if row.amount_cents is None:
        return (
            f"Qual foi o total pago em reais no cupom {ref} ({row.description})?\n"
            f"Pode responder assim: “valor {ref} 89,90”."
        )
    if row.day is None:
        return (
            f"Qual foi a data da compra de {money(row.amount_cents)} em {row.description}?\n"
            f"Responda com “data {ref}” e o dia/mês/ano."
        )
    existing = db.scalar(
        select(LedgerEntry.inbound_id)
        .where(
            LedgerEntry.route == row.route,
            LedgerEntry.kind == "expense",
            LedgerEntry.day == row.day,
            LedgerEntry.amount == row.amount_cents,
            LedgerEntry.searchable == fold(row.description),
        )
        .limit(1)
    )
    warning = (
        f"\n\nJá existe um gasto parecido (F{existing}). Confira se são compras diferentes."
        if existing is not None
        else ""
    )
    return (
        f"Li {money(row.amount_cents)} no cupom de {row.description}.\n"
        f"{row.day:%d/%m/%Y} · Cupom {ref}{warning}\n\n"
        f"Se estiver certo, responda “confirmar cupom {ref}”.\n"
        f"Para ajustar, mande “valor {ref}” ou “data {ref}” com a correção.\n"
        f"Para descartar, mande “cancelar cupom {ref}”."
    )


def propose_receipt(db: Session, turn: Turn, reading: ReceiptReading, now: datetime) -> str:
    image = db.get(Inbound, turn.id)
    if (
        turn.kind != "image"
        or not turn.media
        or db.get(Owner, 1) is None
        or image is None
        or image.kind != "image"
        or image.chat_id != turn.chat_id
    ):
        return "Vincule o dono pelo painel antes de registrar um cupom."
    digest = hashlib.sha256(turn.media).hexdigest()
    previous = db.scalar(
        select(ReceiptDraft).where(
            ReceiptDraft.route == turn.chat_id, ReceiptDraft.digest == digest
        )
    )
    if previous is not None:
        return "Já recebi esta mesma foto.\n\n" + _show(db, previous)
    if reading.currency == "other":
        return "Este cupom parece estar em outra moeda. Para registrar uma despesa, informe o valor efetivamente pago em reais."
    today = (turn.received_at or now).astimezone(ZONE).date()
    row = ReceiptDraft(
        image_id=turn.id,
        route=turn.chat_id,
        digest=digest,
        state="pending",
        day=read_date(reading.day, today),
        amount_cents=paid_amount(reading),
        description=_description(reading.merchant),
    )
    db.add(row)
    return _show(db, row)


def reply_receipt(db: Session, turn: Turn, now: datetime) -> str | None:
    if turn.kind not in ("text", "audio"):
        return None
    raw = unicodedata.normalize("NFC", " ".join(turn.text.split()))
    command = DECISION.fullmatch(fold(raw))
    if not command:
        return None
    identifier = int(command["id"])
    if identifier > 2_147_483_647:
        return "Não encontrei esse cupom neste vínculo."
    source = db.get(Inbound, turn.id)
    owner = db.get(Owner, 1)
    if source is None or source.chat_id != turn.chat_id or owner is None:
        return None
    row = db.scalar(
        select(ReceiptDraft)
        .where(ReceiptDraft.image_id == identifier, ReceiptDraft.route == turn.chat_id)
        .with_for_update()
    )
    if row is None:
        return "Não encontrei esse cupom neste vínculo."
    action = command["action"]
    field = command["field"]
    if (
        action in ("confirmar", "confirme", "confirmo", "cancele", "cancelar", "reabra", "reabrir")
        and field
    ):
        return f"Envie apenas a ação e o código do cupom C{identifier}."
    if row.state == "cancelled" and action in ("reabra", "reabrir"):
        row.state = "pending"
        return _show(db, row)
    if row.state != "pending":
        return _show(db, row)
    if action in ("cancele", "cancelar"):
        row.state = "cancelled"
        return f"Cupom C{identifier} descartado."
    if action == "valor":
        try:
            if not field or not VALUE.fullmatch(field):
                raise FinanceInput
            row.amount_cents = amount(field)[0]
        except FinanceInput:
            return f"Qual o valor exato em reais? Por exemplo, “valor C{identifier} 42,50”."
    elif action == "data":
        today = (turn.received_at or now).astimezone(ZONE).date()
        day = today if field == "hoje" else read_date(field, today)
        if day is None:
            return f"Informe uma data válida da compra em dia/mês/ano, como “data C{identifier} {today:%d/%m/%Y}”."
        row.day = day
    elif action == "descricao":
        if not field or len(field) > 180:
            return f"Envie “descrição C{identifier}” e o nome da compra em até 180 caracteres."
        row.description = _description(raw[command.start("field") : command.end("field")])
    elif action in ("confirmar", "confirmo", "confirme") and row.day and row.amount_cents:
        try:
            entry = record_expense(
                db,
                inbound_id=row.image_id,
                route=row.route,
                day=row.day,
                amount_cents=row.amount_cents,
                description=row.description,
            )
        except Problem as error:
            return error.message
        row.state = "confirmed"
        confirmation = f"Despesa registrada (F{entry.inbound_id}) · {entry.description} · {entry.day:%d/%m/%Y} · {money(entry.amount)}."
        return expense_confirmation(
            db,
            entry,
            confirmation,
            context=fold(db.get_one(Inbound, row.image_id).text),
            casual=Preferences.for_owner(owner).casual,
        )
    return _show(db, row)
