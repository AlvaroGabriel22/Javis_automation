"""Proposta do dono antes de um cupom entrar no caixa."""

from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from jarvis.adapters.persistence.models import Base


class ReceiptDraft(Base):
    __tablename__ = "receipt_drafts"
    __table_args__ = (
        sa.UniqueConstraint("route", "digest", name="uq_receipt_image_route"),
        sa.CheckConstraint("state IN ('pending','confirmed','cancelled')", name="ck_receipt_state"),
        sa.CheckConstraint("amount_cents IS NULL OR amount_cents > 0", name="ck_receipt_amount"),
    )

    image_id: Mapped[int] = mapped_column(sa.ForeignKey("wa_inbox.id"), primary_key=True)
    route: Mapped[str] = mapped_column(sa.ForeignKey("wa_chats.chat_id"))
    digest: Mapped[str] = mapped_column(sa.String(64))
    state: Mapped[str] = mapped_column(sa.String(12))
    description: Mapped[str] = mapped_column(sa.String(180))
    day: Mapped[date | None]
    amount_cents: Mapped[int | None] = mapped_column(sa.BigInteger)
