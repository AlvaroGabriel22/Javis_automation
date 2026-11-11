"""Versão do livro-caixa, reservas e registros exportáveis."""

import sqlalchemy as sa
from alembic import op

revision = "0013_s05_ledger"
down_revision = "0012_calendar_issues"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "personal_ledger", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.create_table(
        "personal_reserves",
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("owners.id"), primary_key=True),
        sa.Column("route", sa.String(192), primary_key=True),
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("cents", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("cents >= 0", name="ck_personal_reserve_nonnegative"),
    )
    op.create_table(
        "sheet_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("owners.id"), nullable=False),
        sa.Column("route", sa.String(192), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("description", sa.String(240), nullable=False),
        sa.Column("quantity", sa.String(80), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("owner_id", "route", "request_key", name="uq_sheet_note_request"),
    )
    op.create_index("ix_sheet_note_route", "sheet_notes", ["owner_id", "route", "day", "id"])
    op.create_table(
        "sheet_removed_ledger",
        sa.Column(
            "inbound_id",
            sa.Integer(),
            sa.ForeignKey("wa_inbox.id"),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("route", sa.String(192), sa.ForeignKey("wa_chats.chat_id"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
    )
    op.create_index("ix_sheet_removed_route", "sheet_removed_ledger", ["route", "inbound_id"])


def downgrade():
    op.drop_table("sheet_removed_ledger")
    op.drop_table("sheet_notes")
    op.drop_table("personal_reserves")
    op.drop_column("personal_ledger", "version")
