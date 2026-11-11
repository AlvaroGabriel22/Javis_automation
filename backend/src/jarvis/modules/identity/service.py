import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.models import Owner, WebSession
from jarvis.modules.errors import Problem

SESSION_SECONDS = 12 * 60 * 60


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token(raw: str) -> str:
    # Tokens têm 256 bits aleatórios. O cliente pode ver esse derivado sem ver o cookie.
    return digest("jarvis-csrf-v1:" + raw)


def issue_code(db: Session, name: str | None, now: datetime) -> str:
    if name is not None:
        name = name.strip()
        if not 1 <= len(name) <= 120 or "\x00" in name:
            raise ValueError("O nome deve ter de 1 a 120 caracteres.")
    owner = db.get(Owner, 1)
    if owner is None:
        owner = Owner(id=1, name=name or "Proprietário")
        db.add(owner)
    elif name is not None and name != owner.name:
        raise ValueError("O dono já está cadastrado. Omita --name para emitir outro código.")
    raw = secrets.token_urlsafe(32)
    owner.code_hash = digest(raw)
    owner.code_expires = now + timedelta(minutes=10)
    db.commit()
    return raw


def login(db: Session, code: str, now: datetime) -> tuple[str, Owner]:
    # Consumo e sessão ficam na mesma transação. Duas conexões não reutilizam o código.
    changed = db.scalar(
        update(Owner)
        .where(Owner.id == 1, Owner.code_hash == digest(code), Owner.code_expires > now)
        .values(code_hash=None, code_expires=None)
        .returning(Owner.id)
    )
    if changed is None:
        raise Problem(401, "invalid_code", "Código inválido, vencido ou já utilizado.")
    owner = db.get_one(Owner, changed)
    raw = secrets.token_urlsafe(32)
    db.execute(delete(WebSession).where(WebSession.expires <= now))
    db.add(
        WebSession(
            owner_id=owner.id,
            token_hash=digest(raw),
            expires=now + timedelta(seconds=SESSION_SECONDS),
        )
    )
    db.commit()
    return raw, owner


def session_owner(db: Session, raw: str, now: datetime) -> Owner:
    if not 1 <= len(raw) <= 128:
        raise Problem(401, "unauthorized", "Entre com um código local.")
    owner = db.scalar(
        select(Owner)
        .join(WebSession)
        .where(WebSession.token_hash == digest(raw), WebSession.expires > now)
    )
    if owner is None:
        raise Problem(401, "unauthorized", "Sua sessão expirou. Entre com outro código local.")
    return owner


def logout(db: Session, raw: str) -> None:
    db.execute(delete(WebSession).where(WebSession.token_hash == digest(raw)))
    db.commit()
