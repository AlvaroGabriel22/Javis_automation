"""Agenda pessoal de uma instalação com um proprietário."""

from datetime import date, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from jarvis.adapters.persistence.models import Base, UTCDateTime


class CalendarSource(Base):
    __tablename__ = "calendar_sources"
    id: Mapped[str] = mapped_column(sa.String(512), primary_key=True)
    provider: Mapped[str] = mapped_column(sa.String(12), default="google", server_default="google")
    remote_url: Mapped[str | None] = mapped_column(sa.Text)
    name: Mapped[str] = mapped_column(sa.String(256))
    timezone: Mapped[str] = mapped_column(sa.String(120), default="America/Manaus")
    writable: Mapped[bool]
    selected: Mapped[bool]
    write_default: Mapped[bool]
    chat_target: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    cursor: Mapped[str | None] = mapped_column(sa.Text)
    last_full: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_sync: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error: Mapped[str | None] = mapped_column(sa.String(180))
    available: Mapped[bool] = mapped_column(default=True)


class CalendarImportIssue(Base):
    __tablename__ = "calendar_import_issues"
    source_id: Mapped[str] = mapped_column(
        sa.ForeignKey("calendar_sources.id", ondelete="CASCADE"), primary_key=True
    )
    remote_id: Mapped[str] = mapped_column(sa.String(1024), primary_key=True)
    etag: Mapped[str | None] = mapped_column(sa.String(256))


class CalendarFocus(Base):
    __tablename__ = "calendar_focus"
    route: Mapped[str] = mapped_column(sa.String(192), primary_key=True)
    anchor: Mapped[date]
    period: Mapped[str] = mapped_column(sa.String(5))


class CalendarChoice(Base):
    __tablename__ = "calendar_choices"
    route: Mapped[str] = mapped_column(sa.String(192), primary_key=True)
    event: Mapped[dict] = mapped_column(sa.JSON)
    options: Mapped[dict[str, list[dict[str, str]]]] = mapped_column(sa.JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Entry(Base):
    __tablename__ = "calendar_entries"
    __table_args__ = (
        sa.UniqueConstraint("source_id", "remote_id", name="uq_calendar_remote"),
        sa.Index("ix_calendar_pair_source", "pair_id", "source_id", unique=True),
        sa.Index(
            "ix_calendar_confirmations",
            "route_key",
            sqlite_where=sa.text("confirmation_pending"),
            postgresql_where=sa.text("confirmation_pending"),
        ),
        sa.Index("ix_calendar_times", "starts_at", "ends_at"),
        sa.Index("ix_calendar_parent", "source_id", "parent", "original_at"),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid4)
    pair_id: Mapped[UUID | None] = mapped_column(sa.Uuid())
    confirmation_pending: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    kind: Mapped[str] = mapped_column(sa.String(12))
    title: Mapped[str] = mapped_column(sa.Text)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime())
    all_day: Mapped[bool] = mapped_column(default=False)
    timezone: Mapped[str] = mapped_column(sa.String(120), default="America/Manaus")
    rules: Mapped[list[str]] = mapped_column(sa.JSON, default=list)
    # Identificador do mestre na origem, inclusive para as exceções no Google.
    parent: Mapped[str | None] = mapped_column(sa.String(1024))
    original_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    alert_minutes: Mapped[int | None]
    route_key: Mapped[str | None] = mapped_column(sa.String(192))
    source_id: Mapped[str | None] = mapped_column(sa.ForeignKey("calendar_sources.id"))
    remote_id: Mapped[str | None] = mapped_column(sa.String(1024))
    etag: Mapped[str | None] = mapped_column(sa.String(256))
    state: Mapped[str] = mapped_column(sa.String(16), default="local")
    deleted: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)
    detail: Mapped[str | None] = mapped_column(sa.String(180))
    # Recibo do lembrete avulso: também permite recuperar um aviso após máquina desligada.
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Routine(Base):
    __tablename__ = "calendar_routines"
    owner_id: Mapped[int] = mapped_column(sa.ForeignKey("owners.id"), primary_key=True)
    morning: Mapped[bool] = mapped_column(default=True)
    evening: Mapped[bool] = mapped_column(default=True)
    birthday: Mapped[str | None] = mapped_column(sa.String(5))
    leap_day: Mapped[str] = mapped_column(sa.String(5), default="02-28")


class ICloudLink(Base):
    __tablename__ = "icloud_link"
    id: Mapped[int] = mapped_column(primary_key=True)
    account: Mapped[str] = mapped_column(sa.String(254))
    encrypted: Mapped[str] = mapped_column(sa.Text)
    error: Mapped[str | None] = mapped_column(sa.String(240))
