"""Notas gerais, retiradas da planilha e reserva financeira por vínculo."""

from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from jarvis.adapters.persistence.models import Base


class SheetNote(Base):
    __tablename__ = "sheet_notes"
    __table_args__ = (
        sa.UniqueConstraint("owner_id", "route", "request_key", name="uq_sheet_note_request"),
        sa.Index("ix_sheet_note_route", "owner_id", "route", "day", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(sa.ForeignKey("owners.id"))
    route: Mapped[str] = mapped_column(sa.String(192))
    request_key: Mapped[str] = mapped_column(sa.String(128))
    fingerprint: Mapped[str] = mapped_column(sa.String(64))
    day: Mapped[date]
    description: Mapped[str] = mapped_column(sa.String(240))
    quantity: Mapped[str] = mapped_column(sa.String(80))
    deleted: Mapped[bool] = mapped_column(server_default=sa.false())


class RemovedLedgerRow(Base):
    __tablename__ = "sheet_removed_ledger"
    __table_args__ = (sa.Index("ix_sheet_removed_route", "route", "inbound_id"),)
    inbound_id: Mapped[int] = mapped_column(
        sa.ForeignKey("wa_inbox.id"), primary_key=True, autoincrement=False
    )
    route: Mapped[str] = mapped_column(sa.ForeignKey("wa_chats.chat_id"))
    day: Mapped[date]
    kind: Mapped[str] = mapped_column(sa.String(12))


class MonthlyReserve(Base):
    __tablename__ = "personal_reserves"
    __table_args__ = (sa.CheckConstraint("cents >= 0", name="ck_personal_reserve_nonnegative"),)
    owner_id: Mapped[int] = mapped_column(sa.ForeignKey("owners.id"), primary_key=True)
    route: Mapped[str] = mapped_column(sa.String(192), primary_key=True)
    month: Mapped[date] = mapped_column(primary_key=True)
    cents: Mapped[int] = mapped_column(sa.BigInteger)
