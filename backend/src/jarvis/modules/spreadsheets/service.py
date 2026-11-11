"""Escrita transacional e projeção estável das planilhas do proprietário."""

import hashlib
import json
import re
from datetime import date

from sqlalchemy import (
    BigInteger,
    Boolean,
    delete,
    literal,
    select,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from jarvis.modules.assistant.intent import fold
from jarvis.modules.errors import Problem
from jarvis.modules.messaging.models import Inbound
from jarvis.modules.personal.models import LedgerEntry
from jarvis.modules.spreadsheets.models import MonthlyReserve, RemovedLedgerRow, SheetNote
from jarvis.modules.spreadsheets.rows import SheetRow

MAX_CENTS = 99_999_999_999
TRANSACTIONS = ("expense", "income")
INVALID_CELL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _cell(value: str, limit: int, *, required=False):
    if not isinstance(value, str) or INVALID_CELL.search(value) or len(value.strip()) > limit:
        raise Problem(
            422, "invalid_cell", f"O texto precisa ser legível e ter até {limit} caracteres."
        )
    value = value.strip()
    if required and not value:
        raise Problem(422, "missing_cell", "Informe a descrição do registro.")
    return value


def _validate_cents(value: int, minimum: int = 1):
    if type(value) is not int or not minimum <= value <= MAX_CENTS:
        raise Problem(
            422, "invalid_amount", "Informe um valor válido em reais, até R$ 999.999.999,99."
        )


def _insert(db: Session, model):
    return sqlite_insert(model) if db.get_bind().dialect.name == "sqlite" else pg_insert(model)


def create_note(
    db: Session, route: str, day: date, description: str, quantity: str = "", *, request_key: str
):
    description = _cell(description, 240, required=True)
    quantity = _cell(quantity, 80)
    if not request_key.strip() or len(request_key) > 128:
        raise Problem(422, "invalid_key", "A chave de idempotência deve ter de 1 a 128 caracteres.")
    body = json.dumps([day.isoformat(), description, quantity], ensure_ascii=False)
    fingerprint = hashlib.sha256(body.encode()).hexdigest()
    # A restrição única também protege duas entregas concorrentes do mesmo pedido.
    db.execute(
        _insert(db, SheetNote)
        .values(
            owner_id=1,
            route=route,
            request_key=request_key,
            day=day,
            description=description,
            quantity=quantity,
            fingerprint=fingerprint,
        )
        .on_conflict_do_nothing(index_elements=["owner_id", "route", "request_key"])
    )
    row = db.scalars(
        select(SheetNote).where(
            SheetNote.owner_id == 1, SheetNote.route == route, SheetNote.request_key == request_key
        )
    ).one()
    if row.fingerprint != fingerprint:
        raise Problem(409, "note_key_reused", "Essa chave já foi usada com outro conteúdo.")
    return row


def remove_note(db: Session, route: str, note_id: int):
    # Guarde só a identidade que torna a remoção e o replay conciliáveis.
    found = db.scalar(
        update(SheetNote)
        .where(SheetNote.owner_id == 1, SheetNote.route == route, SheetNote.id == note_id)
        .values(deleted=True, description="", quantity="")
        .returning(SheetNote.id)
    )
    if found is None:
        raise Problem(404, "note_not_found", "Não encontrei essa anotação neste vínculo.")


def correct_amount(
    db: Session,
    route: str,
    inbound_id: int,
    amount_cents: int,
    *,
    expected_version: int | None = None,
):
    _validate_cents(amount_cents)
    scope = (
        LedgerEntry.inbound_id == inbound_id,
        LedgerEntry.route == route,
        LedgerEntry.kind.in_(TRANSACTIONS),
    )
    # Verificação e escrita no mesmo UPDATE, inclusive se duas abas editarem a mesma linha.
    stmt = update(LedgerEntry).where(*scope)
    if expected_version is not None:
        stmt = stmt.where(LedgerEntry.version == expected_version)
    row = db.scalar(
        stmt.values(amount=amount_cents, version=LedgerEntry.version + 1)
        .returning(LedgerEntry)
        .execution_options(populate_existing=True)
    )
    if row is not None:
        return row
    if expected_version is not None and db.scalar(select(LedgerEntry.inbound_id).where(*scope)):
        raise Problem(
            409, "ledger_version", "Esse lançamento mudou. Recarregue os dados antes de corrigir."
        )
    raise Problem(
        404, "ledger_not_found", "Não encontrei esse lançamento financeiro neste vínculo."
    )


def remove_ledger(db: Session, route: str, inbound_id: int):
    # A mesma trava protege contra uma nova entrega da confirmação de um cupom removido.
    db.execute(
        select(Inbound.id)
        .where(Inbound.id == inbound_id, Inbound.chat_id == route)
        .with_for_update()
    )
    row = db.execute(
        delete(LedgerEntry)
        .where(LedgerEntry.inbound_id == inbound_id, LedgerEntry.route == route)
        .returning(LedgerEntry.day, LedgerEntry.kind)
    ).one_or_none()
    if row is None:
        raise Problem(404, "ledger_not_found", "Não encontrei esse lançamento neste vínculo.")
    if row.kind in TRANSACTIONS:
        db.add(RemovedLedgerRow(inbound_id=inbound_id, route=route, day=row.day, kind=row.kind))


def record_expense(
    db: Session, *, inbound_id: int, route: str, day: date, amount_cents: int, description: str
):
    """Confirma um cupom ligado à foto original, preservando correções e remoções posteriores."""
    _validate_cents(amount_cents)
    description = _cell(description, 240, required=True)
    origin = db.scalar(
        select(Inbound.id)
        .where(Inbound.id == inbound_id, Inbound.chat_id == route, Inbound.kind == "image")
        .with_for_update()
    )
    if origin is None:
        raise Problem(404, "receipt_not_found", "Não encontrei esse cupom neste vínculo.")
    if db.get(RemovedLedgerRow, inbound_id):
        raise Problem(409, "receipt_removed", "A despesa deste cupom já foi excluída.")
    db.execute(
        _insert(db, LedgerEntry)
        .values(
            inbound_id=inbound_id,
            route=route,
            day=day,
            kind="expense",
            amount=amount_cents,
            description=description,
            searchable=fold(description),
        )
        .on_conflict_do_nothing(index_elements=["inbound_id"])
    )
    row = db.get_one(LedgerEntry, inbound_id)
    if row.route != route or row.kind != "expense":
        raise Problem(
            409, "receipt_conflict", "Essa mensagem já está vinculada a outro lançamento."
        )
    return row


def set_reserve(db: Session, route: str, month: date, cents: int):
    _validate_cents(cents, minimum=0)
    month = month.replace(day=1)
    stmt = _insert(db, MonthlyReserve).values(owner_id=1, route=route, month=month, cents=cents)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["owner_id", "route", "month"], set_={"cents": cents}
        )
    )
    return db.get_one(MonthlyReserve, (1, route, month), populate_existing=True)


def reserve_cents(db: Session, route: str, month: date) -> int:
    value = db.scalar(
        select(MonthlyReserve.cents)
        .where(
            MonthlyReserve.owner_id == 1,
            MonthlyReserve.route == route,
            MonthlyReserve.month <= month.replace(day=1),
        )
        .order_by(MonthlyReserve.month.desc())
        .limit(1)
    )
    return value or 0


def snapshot(db: Session, route: str) -> list[SheetRow]:
    """Uma única instrução SQL captura lançamentos, anotações e retiradas no mesmo instante."""
    financial = select(
        literal("F").label("prefix"),
        LedgerEntry.inbound_id.label("row_id"),
        LedgerEntry.day,
        LedgerEntry.kind,
        LedgerEntry.description,
        LedgerEntry.amount.label("cents"),
        literal("").label("quantity"),
        literal(False, Boolean).label("deleted"),
    ).where(LedgerEntry.route == route, LedgerEntry.kind.in_(TRANSACTIONS))
    removed = select(
        literal("F"),
        RemovedLedgerRow.inbound_id,
        RemovedLedgerRow.day,
        RemovedLedgerRow.kind,
        literal(""),
        literal(None, BigInteger),
        literal(""),
        literal(True),
    ).where(RemovedLedgerRow.route == route)
    notes = select(
        literal("P"),
        SheetNote.id,
        SheetNote.day,
        literal("note"),
        SheetNote.description,
        literal(None, BigInteger),
        SheetNote.quantity,
        SheetNote.deleted,
    ).where(SheetNote.owner_id == 1, SheetNote.route == route)
    rows = union_all(financial, removed, notes).order_by("day", "prefix", "row_id")
    return [
        SheetRow(
            f"{r.prefix}{r.row_id}", r.day, r.kind, r.description, r.cents, r.quantity, r.deleted
        )
        for r in db.execute(rows)
    ]
