"""Preferência de figurinhas espontâneas da conversa privada do dono."""

import re
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from jarvis.adapters.persistence.models import Base, Owner
from jarvis.modules.assistant.intent import fold
from jarvis.modules.messaging.models import Inbound

if TYPE_CHECKING:
    from jarvis.modules.messaging.queue import Turn


class StickerPreference(Base):
    __tablename__ = "sticker_preferences"
    route: Mapped[str] = mapped_column(sa.ForeignKey("wa_chats.chat_id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(sa.Boolean())
    source_id: Mapped[int] = mapped_column(sa.ForeignKey("wa_inbox.id"))


ITEM = r"(?:figurinhas?|stickers?)(?: (?:espontane[oa]s?|automatic[oa]s?))?"
SEND = r"(?:mandar|enviar|usar)"
DISABLE = re.compile(
    rf"(?:(?:prefiro |vamos ficar )?sem {ITEM}|nada de {ITEM}|"
    rf"(?:eu )?nao quero(?: mais)?(?: receber)? {ITEM}|"
    rf"(?:nao (?:me )?(?:mand[ae]|envi[ae]|us[ae])(?: mais)?|(?:par[ae]|deix[ae]) de (?:me )?{SEND}) {ITEM}|"
    rf"(?:so (?:me )?(?:mand[ae]|envi[ae]) )?{ITEM} (?:so |somente |apenas )?(?:se|quando) eu pedir|"
    rf"(?:desative|desliga|desligue) (?:as )?{ITEM})(?: por enquanto)?"
)
ENABLE = re.compile(
    rf"(?:(?:(?:voce |vc )?pode voltar a|volt[ae] a) (?:me )?{SEND} {ITEM}|"
    rf"(?:(?:voce |vc )?pode (?:me )?{SEND} {ITEM}|{ITEM}) "
    rf"(?:de novo|novamente|as vezes|de vez em quando|quando (?:fizer sentido|combinar))|"
    rf"(?:ative|ative de novo|reative|ligue) (?:as )?{ITEM})"
)
INTRO = re.compile(r"^(?:(?:oi[, ]+)?jarvis[,!. ]+|por (?:favor|gentileza)[, ]+|eu prefiro que )+")
POLITE = re.compile(r"[, ]+(?:por favor|por gentileza)$")


def requested_preference(text: str) -> bool | None:
    if not text or len(text) > 250:
        return None
    raw = fold(" ".join(text.split())).strip(" .!,?")
    raw = POLITE.sub("", INTRO.sub("", raw))
    if DISABLE.fullmatch(raw):
        return False
    if ENABLE.fullmatch(raw):
        return True
    return None


def allows_reactions(db: Session, route: str) -> bool:
    enabled = db.scalar(
        sa.select(StickerPreference.enabled).where(StickerPreference.route == route)
    )
    return enabled is not False


def handle_preference(db: Session, turn: "Turn") -> str | None:
    if turn.kind not in ("text", "audio"):
        return None
    enabled = requested_preference(turn.text)
    if enabled is None:
        return None
    source = db.get(Inbound, turn.id)
    if (
        not source
        or source.kind != turn.kind
        or source.chat_id != turn.chat_id
        or db.get(Owner, 1) is None
    ):
        return None
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    values = {"enabled": enabled, "source_id": turn.id}
    db.execute(
        insert(StickerPreference)
        .values(route=turn.chat_id, **values)
        .on_conflict_do_update(
            index_elements=[StickerPreference.route],
            set_=values,
            where=StickerPreference.source_id < turn.id,
        )
    )
    return (
        "Combinado. De vez em quando mando uma quando combinar com a conversa."
        if enabled
        else "Fechado. Só mando figurinha se você pedir."
    )
