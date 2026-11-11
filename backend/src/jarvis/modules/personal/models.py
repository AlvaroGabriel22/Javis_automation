"""Dados pessoais pertencem ao vínculo que forneceu a informação."""

from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from jarvis.adapters.persistence.models import Base


class PersonalAction(Base):
    __tablename__ = "personal_actions"
    inbound_id: Mapped[int] = mapped_column(sa.ForeignKey("wa_inbox.id"), primary_key=True)
    route: Mapped[str] = mapped_column(sa.ForeignKey("wa_chats.chat_id"), index=True)


class Fact(Base):
    __tablename__ = "personal_facts"
    __table_args__ = (sa.Index("ix_personal_fact_route", "route", "kind", "inbound_id"),)
    inbound_id: Mapped[int] = mapped_column(sa.ForeignKey("wa_inbox.id"), primary_key=True)
    route: Mapped[str] = mapped_column(sa.ForeignKey("wa_chats.chat_id"))
    kind: Mapped[str] = mapped_column(sa.String(12))
    content: Mapped[str] = mapped_column(sa.String(600))
    searchable: Mapped[str] = mapped_column(sa.String(600))


class LedgerEntry(Base):
    __tablename__ = "personal_ledger"
    __table_args__ = (
        sa.CheckConstraint("amount > 0", name="ck_personal_ledger_positive"),
        sa.CheckConstraint(
            "kind in ('expense','income','budget','monthly')", name="ck_personal_ledger_kind"
        ),
        sa.Index("ix_personal_ledger_period", "route", "kind", "day", "inbound_id"),
    )
    inbound_id: Mapped[int] = mapped_column(sa.ForeignKey("wa_inbox.id"), primary_key=True)
    route: Mapped[str] = mapped_column(sa.ForeignKey("wa_chats.chat_id"))
    day: Mapped[date]
    amount: Mapped[int] = mapped_column(sa.BigInteger)
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    kind: Mapped[str] = mapped_column(sa.String(12))
    description: Mapped[str] = mapped_column(sa.String(240))
    searchable: Mapped[str] = mapped_column(sa.String(240))
