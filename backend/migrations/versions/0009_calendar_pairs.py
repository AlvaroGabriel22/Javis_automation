"""Destinos dos pedidos por conversa e identidade das duas cópias."""

import sqlalchemy as sa
from alembic import op

revision = "0009_calendar_pairs"
down_revision = "0008_icloud"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "calendar_focus",
        sa.Column("route", sa.String(192), primary_key=True),
        sa.Column("anchor", sa.Date(), nullable=False),
        sa.Column("period", sa.String(5), nullable=False),
    )
    op.add_column(
        "calendar_sources",
        sa.Column("chat_target", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("calendar_entries", sa.Column("pair_id", sa.Uuid()))
    op.add_column(
        "calendar_entries",
        sa.Column("confirmation_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_calendar_confirmations",
        "calendar_entries",
        ["route_key"],
        sqlite_where=sa.text("confirmation_pending"),
        postgresql_where=sa.text("confirmation_pending"),
    )
    op.create_index(
        "ix_calendar_pair_source", "calendar_entries", ["pair_id", "source_id"], unique=True
    )


def downgrade():
    op.drop_table("calendar_focus")
    op.drop_index("ix_calendar_confirmations", table_name="calendar_entries")
    op.drop_index("ix_calendar_pair_source", table_name="calendar_entries")
    op.drop_column("calendar_entries", "confirmation_pending")
    op.drop_column("calendar_entries", "pair_id")
    op.drop_column("calendar_sources", "chat_target")
