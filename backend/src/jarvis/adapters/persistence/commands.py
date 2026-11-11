import hashlib
import json
from collections.abc import Callable
from uuid import UUID, uuid4

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, joinedload

from jarvis.adapters.persistence.models import Command, Contact, Schedule
from jarvis.modules.calendar.models import Entry
from jarvis.modules.errors import Problem


def execute_once[T: (Contact, Schedule, Entry)](
    db: Session,
    owner_id: int,
    key: str,
    model: type[T],
    payload: dict[str, str],
    create: Callable[[UUID], T],
) -> tuple[T, bool]:
    # O banco arbitra a corrida. Nunca há chave confirmada sem o efeito correspondente.
    fingerprint = hashlib.sha256(
        json.dumps(
            [model.__tablename__, payload],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    new_id = uuid4()
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    claim = (
        insert(Command)
        .values(owner_id=owner_id, key=key, fingerprint=fingerprint, result_id=new_id)
        .on_conflict_do_nothing(index_elements=["owner_id", "key"])
        .returning(Command.result_id)
    )
    if db.scalar(claim) is None:
        command = db.get_one(Command, (owner_id, key))
        if command.fingerprint != fingerprint:
            raise Problem(409, "idempotency_conflict", "Esta chave já identifica outro pedido.")
        if model is Contact:
            contact = db.get(Contact, command.result_id)
            if contact is None or contact.removed:
                raise Problem(410, "contact_removed", "O contato deste pedido já foi excluído.")
        result = db.get_one(model, command.result_id)
        if isinstance(result, Entry) and result.deleted:
            raise Problem(410, "event_removed", "O item deste pedido já foi cancelado.")
        return result, False
    result = create(new_id)
    db.add(result)
    db.commit()
    return result, True


def page[T: (Contact, Schedule)](
    db: Session, owner: int, model: type[T], cursor: UUID | None, limit: int
) -> tuple[list[T], UUID | None]:
    order = Contact.name if model is Contact else Schedule.scheduled_at
    query = select(model).where(model.owner_id == owner)
    if model is Contact:
        query = query.where(Contact.removed.is_(False))
    else:
        query = query.options(joinedload(Schedule.recipient))
    if cursor is not None:
        anchor = db.scalar(select(model).where(model.owner_id == owner, model.id == cursor))
        if anchor is None:
            raise Problem(422, "invalid_cursor", "Cursor inválido para esta lista.")
        value = anchor.name if isinstance(anchor, Contact) else anchor.scheduled_at
        query = query.where(tuple_(order, model.id) > (value, anchor.id))
    rows = list(db.scalars(query.order_by(order, model.id).limit(limit + 1)))
    return rows[:limit], rows[limit - 1].id if len(rows) > limit else None
