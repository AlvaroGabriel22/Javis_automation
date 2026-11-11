"""Histórico e reservas duráveis do canal privado do proprietário."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from jarvis.adapters.persistence.models import Base, UTCDateTime


class Chat(Base):
    __tablename__ = "wa_chats"
    chat_id: Mapped[str] = mapped_column(sa.String(192), primary_key=True)


class Inbound(Base):
    __tablename__ = "wa_inbox"
    __table_args__ = (
        sa.UniqueConstraint("chat_id", "message_id", name="uq_wa_inbox_event"),
        sa.CheckConstraint(
            "state IN ('pending','processing','completed','failed')", name="ck_wa_in_state"
        ),
        sa.Index("ix_wa_in_due", "state", "available_at", "id"),
        sa.Index("ix_wa_in_chat", "chat_id", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[str] = mapped_column(sa.ForeignKey("wa_chats.chat_id"))
    message_id: Mapped[str] = mapped_column(sa.String(192))
    text: Mapped[str] = mapped_column(sa.Text)
    kind: Mapped[str] = mapped_column(sa.String(10), default="text", server_default="text")
    media: Mapped[bytes | None] = mapped_column(sa.LargeBinary)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())
    state: Mapped[str] = mapped_column(sa.String(16))
    available_at: Mapped[datetime] = mapped_column(UTCDateTime())
    attempts: Mapped[int]
    token: Mapped[str | None] = mapped_column(sa.String(48))
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(sa.String(48))


class Outbound(Base):
    __tablename__ = "wa_outbox"
    __table_args__ = (
        sa.CheckConstraint(
            "state IN ('ready','transmitting','accepted','failed','uncertain')",
            name="ck_wa_out_state",
        ),
        sa.Index("ix_wa_out_due", "state", "available_at", "id"),
    )
    # Um único efeito de resposta por entrada. O destinatário vem do inbox.
    id: Mapped[int] = mapped_column(
        sa.ForeignKey("wa_inbox.id"), primary_key=True, autoincrement=False
    )
    text: Mapped[str] = mapped_column(sa.Text)
    state: Mapped[str] = mapped_column(sa.String(16))
    available_at: Mapped[datetime] = mapped_column(UTCDateTime())
    attempts: Mapped[int]
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    token: Mapped[str | None] = mapped_column(sa.String(48))
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    external_id: Mapped[str | None] = mapped_column(sa.String(192))


class DeliveryAttempt(Base):
    __tablename__ = "wa_delivery_attempts"
    __table_args__ = (
        sa.CheckConstraint(
            "outcome IN ('transmitting','not_sent','uncertain','accepted')", name="ck_wa_attempt"
        ),
    )
    outbound_id: Mapped[int] = mapped_column(
        sa.ForeignKey("wa_outbox.id"), primary_key=True, autoincrement=False
    )
    attempt: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    outcome: Mapped[str] = mapped_column(sa.String(16))
    external_id: Mapped[str | None] = mapped_column(sa.String(192))


class StickerReply(Base):
    """Resposta de mídia escolhida localmente na mesma transação do turno."""

    __tablename__ = "wa_sticker_replies"
    inbound_id: Mapped[int] = mapped_column(
        sa.ForeignKey("wa_inbox.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    sticker_key: Mapped[str] = mapped_column(sa.String(32))


class ChatReaction(Base):
    """Proposta da IA. A transmissão opcional somente existe após o aceite do texto."""

    __tablename__ = "wa_chat_reactions"
    turn_id: Mapped[int] = mapped_column(
        sa.ForeignKey("wa_inbox.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    sticker_key: Mapped[str] = mapped_column(sa.String(32))
    planned_at: Mapped[datetime] = mapped_column(UTCDateTime())
    delivery_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("wa_inbox.id", ondelete="SET NULL"), unique=True
    )
