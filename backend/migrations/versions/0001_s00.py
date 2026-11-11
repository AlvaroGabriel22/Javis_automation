"""Dono local, sessões, contatos, agendamentos e deduplicação de comandos."""

import sqlalchemy as sa
from alembic import op

revision = "0001_s00"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    instant = sa.DateTime(timezone=True)
    op.create_table(
        "owners",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("code_hash", sa.String(64)),
        sa.Column("code_expires", instant),
        sa.CheckConstraint("id = 1", name="ck_single_owner"),
    )
    op.create_table(
        "web_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("owners.id"), nullable=False),
        sa.Column("expires", instant, nullable=False),
    )
    op.create_index("ix_web_sessions_expires", "web_sessions", ["expires"])
    op.create_table(
        "contacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("owners.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("phone", sa.String(16), nullable=False),
        sa.UniqueConstraint("owner_id", "phone", name="uq_contacts_phone"),
    )
    op.create_index("ix_contacts_page", "contacts", ["owner_id", "name", "id"])
    op.create_table(
        "schedules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("owners.id"), nullable=False),
        sa.Column("contact_id", sa.Uuid(), sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("scheduled_at", instant, nullable=False),
        sa.Column("created_at", instant, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.CheckConstraint("status IN ('scheduled','cancelled')", name="ck_schedule_status"),
    )
    op.create_index("ix_schedules_page", "schedules", ["owner_id", "scheduled_at", "id"])
    op.create_table(
        "commands",
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("owners.id"), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("result_id", sa.Uuid(), nullable=False),
    )


def downgrade():
    for table in ("commands", "schedules", "contacts", "web_sessions", "owners"):
        op.drop_table(table)
