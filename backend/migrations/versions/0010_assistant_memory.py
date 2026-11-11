"""Memórias verificáveis, livro-caixa pessoal e busca no histórico do vínculo."""

import sqlalchemy as sa
from alembic import op

revision = "0010_assistant_memory"
down_revision = "0009_calendar_pairs"
branch_labels = None
depends_on = None


def upgrade():
    def source():
        return sa.Column(
            "inbound_id",
            sa.Integer(),
            sa.ForeignKey("wa_inbox.id"),
            primary_key=True,
            autoincrement=False,
        )

    def route():
        return sa.Column("route", sa.String(192), sa.ForeignKey("wa_chats.chat_id"), nullable=False)

    op.create_table("personal_actions", source(), route())
    op.create_index("ix_personal_actions_route", "personal_actions", ["route"])
    op.create_table(
        "personal_facts",
        source(),
        route(),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("content", sa.String(600), nullable=False),
        sa.Column("searchable", sa.String(600), nullable=False),
    )
    op.create_index("ix_personal_fact_route", "personal_facts", ["route", "kind", "inbound_id"])
    op.create_table(
        "personal_ledger",
        source(),
        route(),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("description", sa.String(240), nullable=False),
        sa.Column("searchable", sa.String(240), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_personal_ledger_positive"),
        sa.CheckConstraint(
            "kind in ('expense','income','budget','monthly')", name="ck_personal_ledger_kind"
        ),
    )
    op.create_index(
        "ix_personal_ledger_period", "personal_ledger", ["route", "kind", "day", "inbound_id"]
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE VIRTUAL TABLE personal_history_fts USING fts5(body, tokenize='unicode61 remove_diacritics 2')"
        )
        op.execute(
            "INSERT INTO personal_history_fts (rowid, body) SELECT id,text FROM wa_inbox WHERE kind IN ('text','audio')"
        )
        op.execute("""CREATE TRIGGER personal_history_insert AFTER INSERT ON wa_inbox
            WHEN new.kind IN ('text','audio') BEGIN
            INSERT INTO personal_history_fts(rowid,body) VALUES (new.id,new.text); END""")
        op.execute("""CREATE TRIGGER personal_history_update AFTER UPDATE OF text, kind ON wa_inbox BEGIN
            DELETE FROM personal_history_fts WHERE rowid=old.id;
            INSERT INTO personal_history_fts(rowid,body) SELECT new.id,new.text WHERE new.kind IN ('text','audio'); END""")
        op.execute("""CREATE TRIGGER personal_history_delete AFTER DELETE ON wa_inbox BEGIN
            DELETE FROM personal_history_fts WHERE rowid=old.id; END""")
    else:
        op.execute("""CREATE INDEX ix_personal_history_fts ON wa_inbox USING GIN
            (to_tsvector('simple', translate(lower(text), 'áàâãéêíóôõúçü', 'aaaaeeioooucu')))
            WHERE kind IN ('text','audio')""")


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        for trigger in (
            "personal_history_insert",
            "personal_history_update",
            "personal_history_delete",
        ):
            op.execute(f"DROP TRIGGER {trigger}")
        op.execute("DROP TABLE personal_history_fts")
    else:
        op.drop_index("ix_personal_history_fts", table_name="wa_inbox")
    for table in ("personal_ledger", "personal_facts", "personal_actions"):
        op.drop_table(table)
