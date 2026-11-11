"""agenda pessoal, rotinas e sincronização

Revision ID: 0007_calendar
Revises: 0006_owner_and_drafts
Create Date: 2026-11-08 00:39:59.122066

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_calendar"
down_revision: str | Sequence[str] | None = "0006_owner_and_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "calendar_sources",
        sa.Column("id", sa.String(length=512), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("timezone", sa.String(length=120), nullable=False),
        sa.Column("writable", sa.Boolean(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("write_default", sa.Boolean(), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("last_full", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=180), nullable=True),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "calendar_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("all_day", sa.Boolean(), nullable=False),
        sa.Column("timezone", sa.String(length=120), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("parent", sa.String(length=1024), nullable=True),
        sa.Column("original_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alert_minutes", sa.Integer(), nullable=True),
        sa.Column("route_key", sa.String(length=192), nullable=True),
        sa.Column("source_id", sa.String(length=512), nullable=True),
        sa.Column("remote_id", sa.String(length=1024), nullable=True),
        sa.Column("etag", sa.String(length=256), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("detail", sa.String(length=180), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["calendar_sources.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "remote_id", name="uq_calendar_remote"),
    )
    op.create_index(
        "ix_calendar_parent",
        "calendar_entries",
        ["source_id", "parent", "original_at"],
        unique=False,
    )
    op.create_index("ix_calendar_times", "calendar_entries", ["starts_at", "ends_at"], unique=False)
    op.create_table(
        "calendar_routines",
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("morning", sa.Boolean(), nullable=False),
        sa.Column("evening", sa.Boolean(), nullable=False),
        sa.Column("birthday", sa.String(length=5), nullable=True),
        sa.Column("leap_day", sa.String(length=5), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
        ),
        sa.PrimaryKeyConstraint("owner_id"),
    )
    op.add_column("wa_outbox", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("wa_outbox", "expires_at")
    op.drop_table("calendar_routines")
    op.drop_index("ix_calendar_times", table_name="calendar_entries")
    op.drop_index("ix_calendar_parent", table_name="calendar_entries")
    op.drop_table("calendar_entries")
    op.drop_table("calendar_sources")
