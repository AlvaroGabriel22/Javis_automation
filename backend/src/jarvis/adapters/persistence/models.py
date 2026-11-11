from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class UTCDateTime(sa.TypeDecorator[datetime]):
    """TIMESTAMPTZ no PostgreSQL. SQLite registra UTC e não devolve tzinfo."""

    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect):
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("É obrigatório informar o fuso.")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


OwnerTone = Literal["proximo", "neutro"]


class Owner(Base):
    __tablename__ = "owners"
    __table_args__ = (sa.CheckConstraint("id = 1", name="ck_single_owner"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(sa.String(120))
    preferred_name: Mapped[str | None] = mapped_column(sa.String(120))
    gender: Mapped[str] = mapped_column(sa.String(40), server_default="Não informado")
    address_as: Mapped[str] = mapped_column(sa.String(40), server_default="senhor")
    tone: Mapped[OwnerTone] = mapped_column(
        sa.String(7), sa.CheckConstraint("tone IN ('proximo', 'neutro')"), server_default="proximo"
    )
    code_hash: Mapped[str | None] = mapped_column(sa.String(64))
    code_expires: Mapped[datetime | None] = mapped_column(UTCDateTime())


class MessageDraft(Base):
    __tablename__ = "message_drafts"
    route_key: Mapped[str] = mapped_column(sa.String(192), primary_key=True)
    contact_name: Mapped[str] = mapped_column(sa.String(120))
    phone: Mapped[str | None] = mapped_column(sa.String(16))
    choices: Mapped[list[dict[str, str]] | None] = mapped_column(sa.JSON(none_as_null=True))
    body: Mapped[str] = mapped_column(sa.Text)
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    immediate: Mapped[bool] = mapped_column(sa.Boolean())


class WebSession(Base):
    __tablename__ = "web_sessions"
    token_hash: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(sa.ForeignKey("owners.id"))
    expires: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        sa.Index(
            "uq_active_contacts_phone",
            "owner_id",
            "phone",
            unique=True,
            sqlite_where=sa.column("removed").is_(False),
            postgresql_where=sa.column("removed").is_(False),
        ),
        sa.Index("ix_contacts_page", "owner_id", "name", "id"),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid4)
    owner_id: Mapped[int] = mapped_column(sa.ForeignKey("owners.id"))
    name: Mapped[str] = mapped_column(sa.String(120))
    phone: Mapped[str] = mapped_column(sa.String(16))
    removed: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    source_route: Mapped[str | None] = mapped_column(sa.String(192))


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('scheduled','cancelled','sending','accepted','needs_review','uncertain','failed')",
            name="ck_schedule_status",
        ),
        sa.Index("ix_schedules_due", "route_key", "status", "scheduled_at"),
        sa.Index("ix_schedules_page", "owner_id", "scheduled_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid4)
    owner_id: Mapped[int] = mapped_column(sa.ForeignKey("owners.id"))
    contact_id: Mapped[UUID] = mapped_column(sa.ForeignKey("contacts.id"))
    recipient: Mapped[Contact] = relationship(viewonly=True)
    text: Mapped[str] = mapped_column(sa.Text)
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(sa.String(16))
    route_key: Mapped[str | None] = mapped_column(sa.String(192))
    recurrence: Mapped[str] = mapped_column(sa.String(12), default="none", server_default="none")
    retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    active_attempt: Mapped[UUID | None] = mapped_column(sa.Uuid())
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    result_detail: Mapped[str | None] = mapped_column(sa.String(120))
    relay_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    @property
    def armed(self) -> bool:
        return self.route_key is not None


class Command(Base):
    __tablename__ = "commands"
    owner_id: Mapped[int] = mapped_column(sa.ForeignKey("owners.id"), primary_key=True)
    key: Mapped[str] = mapped_column(sa.String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(sa.String(64))
    result_id: Mapped[UUID] = mapped_column(sa.Uuid())


class ContactAlias(Base):
    __tablename__ = "contact_aliases"
    contact_id: Mapped[UUID] = mapped_column(sa.ForeignKey("contacts.id"), primary_key=True)
    alias: Mapped[str] = mapped_column(sa.String(120), primary_key=True)


class DeviceContact(Base):
    __tablename__ = "device_contacts"
    route_key: Mapped[str] = mapped_column(sa.String(192), primary_key=True)
    phone: Mapped[str] = mapped_column(sa.String(16), primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(120))


class ScheduleAttempt(Base):
    __tablename__ = "schedule_attempts"
    schedule_id: Mapped[UUID] = mapped_column(sa.ForeignKey("schedules.id"), primary_key=True)
    token: Mapped[UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid4)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    outcome: Mapped[str] = mapped_column(sa.String(24))
    external_id: Mapped[str | None] = mapped_column(sa.String(192))
