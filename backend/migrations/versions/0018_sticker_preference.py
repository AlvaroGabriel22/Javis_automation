"""Preferência de reação espontânea do vínculo."""

import sqlalchemy as sa
from alembic import op

revision = "0018_sticker_preference"
down_revision = "0017_chat_reactions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sticker_preferences",
        sa.Column("route", sa.String(192), sa.ForeignKey("wa_chats.chat_id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("wa_inbox.id"), nullable=False),
    )


def downgrade():
    op.drop_table("sticker_preferences")
