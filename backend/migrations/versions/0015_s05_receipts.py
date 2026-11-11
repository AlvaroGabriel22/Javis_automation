"""Guarda as propostas de cupom e suas confirmações por vínculo."""

import sqlalchemy as sa
from alembic import op

revision = "0015_s05_receipts"
down_revision = "0014_sticker_reply"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "receipt_drafts",
        sa.Column("image_id", sa.Integer(), sa.ForeignKey("wa_inbox.id"), primary_key=True),
        sa.Column("route", sa.String(192), sa.ForeignKey("wa_chats.chat_id"), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("description", sa.String(180), nullable=False),
        sa.Column("day", sa.Date()),
        sa.Column("amount_cents", sa.BigInteger()),
        sa.UniqueConstraint("route", "digest", name="uq_receipt_image_route"),
        sa.CheckConstraint("state IN ('pending','confirmed','cancelled')", name="ck_receipt_state"),
        sa.CheckConstraint("amount_cents IS NULL OR amount_cents > 0", name="ck_receipt_amount"),
    )


def downgrade():
    op.drop_table("receipt_drafts")
