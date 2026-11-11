"""Preserva o compromisso enquanto o dono escolhe o calendário pelo WhatsApp."""

import sqlalchemy as sa
from alembic import op

revision = "0011_calendar_choice"
down_revision = "0010_assistant_memory"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "calendar_choices",
        sa.Column("route", sa.String(192), primary_key=True),
        sa.Column("event", sa.JSON(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("calendar_choices")
