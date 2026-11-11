"""Identidade da figurinha escolhida para responder a uma entrada privada."""

import sqlalchemy as sa
from alembic import op

revision = "0014_sticker_reply"
down_revision = "0013_s05_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "wa_sticker_replies",
        sa.Column(
            "inbound_id",
            sa.Integer(),
            sa.ForeignKey("wa_inbox.id", ondelete="CASCADE"),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("sticker_key", sa.String(32), nullable=False),
    )


def downgrade():
    op.drop_table("wa_sticker_replies")
