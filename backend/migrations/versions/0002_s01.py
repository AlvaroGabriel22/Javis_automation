"""Caixas de entrada e saída da conversa WhatsApp, com tentativa identificável."""

import sqlalchemy as sa
from alembic import op

revision = "0002_s01"
down_revision = "0001_s00"
branch_labels = None
depends_on = None


def upgrade():
    instant = sa.DateTime(timezone=True)
    op.create_table("wa_chats", sa.Column("chat_id", sa.String(192), primary_key=True))
    op.create_table(
        "wa_inbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.String(192), sa.ForeignKey("wa_chats.chat_id"), nullable=False),
        sa.Column("message_id", sa.String(192), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("received_at", instant, nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("available_at", instant, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(48)),
        sa.Column("lease_until", instant),
        sa.Column("error_code", sa.String(48)),
        sa.UniqueConstraint("chat_id", "message_id", name="uq_wa_inbox_event"),
        sa.CheckConstraint(
            "state IN ('pending','processing','completed','failed')", name="ck_wa_in_state"
        ),
    )
    op.create_index("ix_wa_in_due", "wa_inbox", ["state", "available_at", "id"])
    op.create_index("ix_wa_in_chat", "wa_inbox", ["chat_id", "id"])
    op.create_table(
        "wa_outbox",
        sa.Column(
            "id", sa.Integer(), sa.ForeignKey("wa_inbox.id"), primary_key=True, autoincrement=False
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("available_at", instant, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(48)),
        sa.Column("lease_until", instant),
        sa.Column("external_id", sa.String(192)),
        sa.CheckConstraint(
            "state IN ('ready','transmitting','accepted','failed','uncertain')",
            name="ck_wa_out_state",
        ),
    )
    op.create_index("ix_wa_out_due", "wa_outbox", ["state", "available_at", "id"])
    op.create_table(
        "wa_delivery_attempts",
        sa.Column(
            "outbound_id",
            sa.Integer(),
            sa.ForeignKey("wa_outbox.id"),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("attempt", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("started_at", instant, nullable=False),
        sa.Column("ended_at", instant),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(192)),
        sa.CheckConstraint(
            "outcome IN ('transmitting','not_sent','uncertain','accepted')", name="ck_wa_attempt"
        ),
    )


def downgrade():
    for name in ("wa_delivery_attempts", "wa_outbox", "wa_inbox", "wa_chats"):
        op.drop_table(name)
