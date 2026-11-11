"""Resolução de contatos cadastrados, cartões e agenda da conta vinculada."""

import re
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.models import Contact, ContactAlias, DeviceContact
from jarvis.modules.assistant.intent import fold


@dataclass(frozen=True)
class Recipient:
    name: str
    phone: str


def phone_number(raw: str) -> str | None:
    if not re.fullmatch(r"\+?[\d ()\-.]+", raw.strip(), re.ASCII):
        return None
    digits = re.sub(r"\D", "", raw)
    if not raw.lstrip().startswith("+"):
        if len(digits) in (10, 11):
            digits = "55" + digits
        elif not (digits.startswith("55") and len(digits) in (12, 13)):
            return None
    return "+" + digits if re.fullmatch(r"[1-9]\d{6,14}", digits, re.ASCII) else None


def visible_name(db: Session, contact: Contact, route: str) -> str:
    if contact.source_route in (None, route):
        return contact.name
    device = db.get(DeviceContact, (route, contact.phone))
    return device.name if device else contact.phone


def candidates(db: Session, route: str, query: str) -> list[Recipient]:
    if phone := phone_number(query):
        contact = db.scalar(
            select(Contact).where(
                Contact.owner_id == 1, Contact.removed.is_(False), Contact.phone == phone
            )
        )
        if contact:
            return [Recipient(visible_name(db, contact, route), phone)]
        device = db.get(DeviceContact, (route, phone))
        return [Recipient(device.name if device else phone, phone)]
    term = fold(query.strip())
    if not term:
        return []
    found: dict[str, Recipient] = {}

    def match(name: str, label: str, phone: str):
        normalized = fold(name.strip())
        if normalized == term or normalized.startswith(term + " "):
            found.setdefault(phone, Recipient(label, phone))

    visible = or_(Contact.source_route.is_(None), Contact.source_route == route)
    for row in db.scalars(
        select(Contact).where(Contact.owner_id == 1, Contact.removed.is_(False), visible)
    ):
        match(row.name, row.name, row.phone)
    for alias, name, phone in db.execute(
        select(ContactAlias.alias, Contact.name, Contact.phone)
        .join(Contact, Contact.id == ContactAlias.contact_id)
        .where(Contact.owner_id == 1, Contact.removed.is_(False), visible)
    ):
        match(alias, name, phone)
    for device in db.scalars(select(DeviceContact).where(DeviceContact.route_key == route)):
        match(device.name, device.name, device.phone)
    return sorted(found.values(), key=lambda item: (fold(item.name), item.phone))


def materialize(db: Session, route: str, choice: Recipient) -> Contact:
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    stmt = insert(Contact).values(
        id=uuid4(), owner_id=1, name=choice.name, phone=choice.phone, source_route=route
    )
    return db.scalars(
        stmt.on_conflict_do_update(
            index_elements=["owner_id", "phone"],
            index_where=Contact.removed.is_(False),
            set_={"phone": stmt.excluded.phone},
        ).returning(Contact)
    ).one()


def add_alias(db: Session, route: str, choice: Recipient, alias: str) -> bool:
    contact = materialize(db, route, choice)
    if contact.source_route not in (None, route):
        return False
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    db.execute(
        insert(ContactAlias)
        .values(contact_id=contact.id, alias=alias)
        .on_conflict_do_nothing(index_elements=["contact_id", "alias"])
    )
    return True
