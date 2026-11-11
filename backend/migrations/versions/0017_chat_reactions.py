"""Figurinha espontânea vinculada ao texto confirmado no WhatsApp."""

import sqlalchemy as sa
from alembic import op

revision = "0017_chat_reactions"
down_revision = "0016_s05_exports"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "wa_chat_reactions",
        sa.Column(
            "turn_id",
            sa.Integer(),
            sa.ForeignKey("wa_inbox.id", ondelete="CASCADE"),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("sticker_key", sa.String(32), nullable=False),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "delivery_id",
            sa.Integer(),
            sa.ForeignKey("wa_inbox.id", ondelete="SET NULL"),
            unique=True,
        ),
    )


def downgrade():
    op.drop_table("wa_chat_reactions")
