"""Atualiza as origens antes de responder a uma consulta pelo WhatsApp."""

import fcntl
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from jarvis.adapters.google.client import Google, GoogleError, root
from jarvis.adapters.google.sync import sync
from jarvis.adapters.icloud import account
from jarvis.modules.calendar.models import CalendarSource, ICloudLink
from jarvis.modules.calendar.service import PROVIDERS


def google_connected():
    return (root() / "token.json").is_file()


def refresh_google(engine, google):
    # A mesma trava cobre o processo periódico e as consultas por conversa.
    with (root() / "refresh.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        sync(engine, google)
        return True


def refresh_for_query(engine, providers: frozenset[str] = frozenset()):
    with Session(engine) as db:
        selected = set(db.scalars(select(CalendarSource.provider).where(CalendarSource.selected)))
        apple_linked = bool(db.get(ICloudLink, 1))
    linked = {"google": google_connected(), "icloud": apple_linked}
    unavailable = []
    for provider, connected in linked.items():
        if providers and provider not in providers:
            continue
        if not connected:
            if provider in selected or provider in providers:
                unavailable.append(PROVIDERS[provider])
            continue
        try:
            done = (
                refresh_google(engine, Google()) if provider == "google" else account.sync(engine)
            )
        except (GoogleError, OSError, KeyError, ValueError, OverflowError) as exc:
            logging.warning("Falha na consulta de %s (%s).", provider, type(exc).__name__)
            done = False
        with Session(engine) as db:
            visible = db.scalar(
                select(CalendarSource.id)
                .where(
                    CalendarSource.provider == provider,
                    CalendarSource.selected,
                    CalendarSource.available,
                )
                .limit(1)
            )
        if not done or not visible:
            unavailable.append(PROVIDERS[provider])
    return unavailable
