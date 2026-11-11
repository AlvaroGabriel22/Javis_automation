"""Figurinhas espontâneas acompanham uma resposta de conversa já aceita pelo canal."""

from datetime import datetime, timedelta

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, aliased

from jarvis.modules.messaging.models import ChatReaction, Inbound, Outbound
from jarvis.modules.stickers.catalog import BY_KEY

LIFETIME = timedelta(minutes=3)
COOLDOWN = timedelta(minutes=30)
CONVERSATION_BREAK = timedelta(hours=4)
INTERVENING_TURNS = 4
USER_KINDS = ("audio", "text", "image", "sticker", "card")


def _allows(db: Session, route: str) -> bool:
    from jarvis.modules.stickers.preferences import allows_reactions

    return allows_reactions(db, route)


def superseded(db: Session, route: str, source_id: int, delivery_id: int | None = None) -> bool:
    newer = select(Inbound.id).where(Inbound.chat_id == route, Inbound.id > source_id)
    if delivery_id is not None:
        newer = newer.where(Inbound.id != delivery_id)
    return bool(db.scalar(exists(newer).select()))


def eligible(db: Session, route: str, source_id: int, at: datetime) -> bool:
    source = db.get(Inbound, source_id)
    if (
        not source
        or source.chat_id != route
        or source.kind not in ("text", "audio")
        or source.received_at + LIFETIME <= at
        or not _allows(db, route)
        or superseded(db, route, source_id)
    ):
        return False
    # Um turno por vínculo avança de cada vez na fila, inclusive ao repetir após uma queda.
    latest = db.execute(
        select(ChatReaction.turn_id, ChatReaction.planned_at)
        .join(Inbound, Inbound.id == ChatReaction.turn_id)
        .where(Inbound.chat_id == route)
        .order_by(ChatReaction.turn_id.desc())
        .limit(1)
    ).one_or_none()
    if latest is None or latest.planned_at + CONVERSATION_BREAK <= at:
        return True
    if latest.planned_at + COOLDOWN > at:
        return False
    enough_turns = (
        select(Inbound.id)
        .where(
            Inbound.chat_id == route,
            Inbound.id > latest.turn_id,
            Inbound.id < source_id,
            Inbound.kind.in_(USER_KINDS),
            Inbound.state == "completed",
        )
        .limit(1)
        .offset(INTERVENING_TURNS - 1)
    )
    return db.scalar(enough_turns) is not None


def plan(db: Session, route: str, source_id: int, key: str, at: datetime) -> None:
    if key in BY_KEY and eligible(db, route, source_id, at):
        db.add(ChatReaction(turn_id=source_id, sticker_key=key, planned_at=at))


def after_acceptance(db: Session, source_id: int, at: datetime) -> None:
    proposal = db.get(ChatReaction, source_id)
    if proposal is None or proposal.delivery_id is not None or proposal.sticker_key not in BY_KEY:
        return
    source = db.get_one(Inbound, source_id)
    expires = source.received_at + LIFETIME
    if (
        expires <= at
        or not _allows(db, source.chat_id)
        or superseded(db, source.chat_id, source_id)
    ):
        return
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    notice_id = db.scalar(
        insert(Inbound)
        .values(
            chat_id=source.chat_id,
            message_id=f"jarvis:reaction:{source_id}",
            kind="notice",
            text="[Figurinha da conversa]",
            received_at=at,
            available_at=at,
            state="completed",
            attempts=0,
        )
        .on_conflict_do_nothing()
        .returning(Inbound.id)
    )
    if notice_id is not None:
        db.add(
            Outbound(
                id=notice_id,
                text=f"[Figurinha · {BY_KEY[proposal.sticker_key].name}]",
                state="ready",
                attempts=0,
                available_at=at,
                expires_at=expires,
            )
        )
        proposal.delivery_id = notice_id


def pending_is_current(db: Session, route: str, source_id: int, delivery_id: int) -> bool:
    return _allows(db, route) and not superseded(db, route, source_id, delivery_id)


def drop_superseded(db: Session, route: str) -> None:
    """Uma figurinha em backoff não deixa um novo pedido esperando."""
    newer = aliased(Inbound)
    irrelevant = exists(
        select(newer.id).where(
            newer.chat_id == route,
            newer.id > ChatReaction.turn_id,
            newer.id != ChatReaction.delivery_id,
        )
    ).correlate(ChatReaction)
    attached = exists(
        select(ChatReaction.turn_id).where(
            ChatReaction.delivery_id == Outbound.id,
            irrelevant,
        )
    ).correlate(Outbound)
    route_ids = select(Inbound.id).where(Inbound.chat_id == route)
    db.execute(
        update(Outbound)
        .where(Outbound.state == "ready", Outbound.id.in_(route_ids), attached)
        .values(state="failed")
    )
