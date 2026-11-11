import json
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.commands import execute_once, page
from jarvis.adapters.persistence.models import Contact, ContactAlias, Schedule
from jarvis.modules.errors import Problem


def create_contact(
    db: Session, owner: int, key: str, name: str, phone: str, aliases: list[str] | None = None
) -> tuple[Contact, bool]:
    names = sorted(set(aliases or []))
    if any("\x00" in name for name in names):
        raise Problem(422, "invalid_alias", "Confira os apelidos.")
    payload = {"name": name, "phone": phone}
    if names:
        payload["aliases"] = json.dumps(names, ensure_ascii=False)

    def create(cid: UUID) -> Contact:
        contact = Contact(id=cid, owner_id=owner, name=name, phone=phone)
        db.add(contact)
        db.flush()
        db.add_all(ContactAlias(contact_id=cid, alias=alias) for alias in names)
        return contact

    try:
        return execute_once(
            db,
            owner,
            key,
            Contact,
            payload,
            create,
        )
    except IntegrityError:
        db.rollback()
        if db.scalar(
            select(Contact.id).where(
                Contact.owner_id == owner, Contact.phone == phone, Contact.removed.is_(False)
            )
        ):
            raise Problem(409, "phone_exists", "Já existe um contato com este telefone.") from None
        raise


def require_contact(db: Session, owner: int, cid: UUID) -> Contact:
    contact = db.scalar(
        select(Contact)
        .where(Contact.id == cid, Contact.owner_id == owner, Contact.removed.is_(False))
        .with_for_update(read=True)
    )
    if contact is None:
        raise Problem(404, "contact_not_found", "Contato não encontrado.")
    return contact


def list_contacts(db: Session, owner: int, cursor: UUID | None, limit: int):
    return page(db, owner, Contact, cursor, limit)


def delete_contact(db: Session, owner: int, cid: UUID) -> None:
    hidden = db.scalar(
        update(Contact)
        .where(Contact.id == cid, Contact.owner_id == owner, Contact.removed.is_(False))
        .values(removed=True)
        .returning(Contact.id)
    )
    if hidden is not None:
        db.execute(delete(ContactAlias).where(ContactAlias.contact_id == cid))
        # O histórico e os envios existentes ainda usam a identidade original.
        referenced = select(Schedule.id).where(Schedule.contact_id == cid).exists()
        db.execute(delete(Contact).where(Contact.id == cid, Contact.owner_id == owner, ~referenced))
    db.commit()
