"""Vínculo por perfil. A senha específica só sai da máquina em requisições ao CalDAV Apple."""

import fcntl
import os
from contextlib import contextmanager

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from jarvis.adapters.icloud.client import AppleError, CalDAV
from jarvis.adapters.icloud.sync import PENDING, discover, sync_source
from jarvis.adapters.whatsapp.profile import state_dir
from jarvis.modules.calendar.models import CalendarSource, Entry, ICloudLink
from jarvis.modules.errors import Problem


def folder():
    return state_dir().parent / "icloud"


@contextmanager
def exclusive():
    path = folder()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (path / "sync.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def cipher(create=False):
    path = folder() / "key"
    if create and not path.exists():
        with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as f:
            f.write(Fernet.generate_key())
            f.flush()
            os.fsync(f.fileno())
    return Fernet(path.read_bytes())


def status(db):
    link = db.get(ICloudLink, 1)
    return {"account": link.account if link else None, "error": link.error if link else None}


def connect(db, email, password):
    try:
        with exclusive():
            current = db.get(ICloudLink, 1)
            if current and current.account != email:
                raise Problem(
                    409,
                    "icloud_account",
                    "Desconecte a Conta Apple anterior antes de vincular outra.",
                )
            calendars = CalDAV(email, password).calendars()
            if not calendars:
                raise Problem(
                    422, "icloud_empty", "Crie ou ative um calendário no iCloud e tente novamente."
                )
            link = current or ICloudLink(id=1, account=email)
            link.encrypted = cipher(create=True).encrypt(password.encode()).decode()
            link.error = None
            db.add(link)
            discover(db, email, calendars)
            db.commit()
    except AppleError as err:
        raise Problem(
            422 if err.status in (401, 403) else 502, "icloud_connection", str(err)
        ) from None
    except BlockingIOError:
        raise Problem(
            409, "icloud_busy", "O iCloud está sincronizando. Tente novamente em instantes."
        ) from None
    return status(db)


def disconnect(db):
    try:
        with exclusive():
            sources = list(
                db.scalars(select(CalendarSource).where(CalendarSource.provider == "icloud"))
            )
            if db.scalar(
                select(Entry.id)
                .where(Entry.source_id.in_([s.id for s in sources]), Entry.state.in_(PENDING))
                .limit(1)
            ):
                raise Problem(
                    409,
                    "icloud_pending",
                    "Aguarde a sincronização das alterações antes de desconectar.",
                )
            for source in sources:
                source.available = source.selected = source.write_default = False
            if link := db.get(ICloudLink, 1):
                db.delete(link)
            db.commit()
    except BlockingIOError:
        raise Problem(
            409, "icloud_busy", "O iCloud está sincronizando. Tente novamente em instantes."
        ) from None
    return status(db)


def sync(engine):
    with Session(engine) as db:
        if not db.get(ICloudLink, 1):
            return
    try:
        with exclusive(), Session(engine) as db:
            link = db.get(ICloudLink, 1)
            if not link:
                return
            try:
                password = cipher().decrypt(link.encrypted.encode()).decode()
                client = CalDAV(link.account, password)
                calendars = client.calendars()
                discover(db, link.account, calendars)
                link.error = None
            except (InvalidToken, OSError, ValueError, AppleError) as err:
                link.error = (
                    str(err)
                    if isinstance(err, AppleError)
                    else "Reconecte a Conta Apple para renovar as credenciais."
                )
                for s in db.scalars(
                    select(CalendarSource).where(CalendarSource.provider == "icloud")
                ):
                    s.error = link.error
                db.commit()
                return
            db.commit()
            ids = list(
                db.scalars(
                    select(CalendarSource.id).where(
                        CalendarSource.provider == "icloud",
                        CalendarSource.available,
                        CalendarSource.selected
                        | CalendarSource.write_default
                        | CalendarSource.id.in_(
                            select(Entry.source_id).where(Entry.state.in_(PENDING))
                        ),
                    )
                )
            )
            db.commit()
            for sid in ids:
                try:
                    sync_source(engine, client, sid)
                except (AppleError, KeyError, ValueError, OverflowError) as err:
                    db.get_one(CalendarSource, sid).error = (
                        str(err)
                        if isinstance(err, AppleError)
                        else "O iCloud retornou um evento incompatível. Confira a agenda."
                    )
                    db.commit()
            return True
    except BlockingIOError:
        return False
