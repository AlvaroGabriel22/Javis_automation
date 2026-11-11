"""Versão dos dados publicáveis e recibos privados de sincronização por vínculo."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from jarvis.adapters.persistence.models import Base, UTCDateTime


class SheetRevision(Base):
    __tablename__ = "sheet_revisions"
    route: Mapped[str] = mapped_column(sa.String(192), primary_key=True)
    revision: Mapped[int] = mapped_column(sa.BigInteger)


class GoogleDestination(Base):
    __tablename__ = "sheet_google_bindings"
    destination: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    route: Mapped[str] = mapped_column(sa.String(192))


class ExportState(Base):
    __tablename__ = "sheet_exports"
    __table_args__ = (sa.CheckConstraint("target in ('excel','google')", name="ck_export_target"),)
    route: Mapped[str] = mapped_column(sa.String(192), primary_key=True)
    target: Mapped[str] = mapped_column(sa.String(8), primary_key=True)
    enabled: Mapped[bool] = mapped_column(sa.Boolean)
    destination: Mapped[str | None] = mapped_column(sa.String(64))
    request_id: Mapped[str] = mapped_column(sa.String(32))
    applied_id: Mapped[str | None] = mapped_column(sa.String(32))
    file_identity: Mapped[str | None] = mapped_column(sa.String(64))
    applied_revision: Mapped[int | None] = mapped_column(sa.BigInteger)
    error: Mapped[str | None] = mapped_column(sa.String(300))
    failures: Mapped[int] = mapped_column(server_default="0")
    retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
