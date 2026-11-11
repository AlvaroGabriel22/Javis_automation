"""Sincronização durável de planilhas, com revisão transacional por vínculo."""

import sqlalchemy as sa
from alembic import op

revision = "0016_s05_exports"
down_revision = "0015_s05_receipts"
branch_labels = None
depends_on = None

TABLES = ("personal_ledger", "sheet_notes", "sheet_removed_ledger")


def bump(route):
    return f"""INSERT INTO sheet_revisions (route, revision) VALUES ({route}, 1)
        ON CONFLICT (route) DO UPDATE SET revision = sheet_revisions.revision + 1;"""


def upgrade():
    op.create_table(
        "sheet_revisions",
        sa.Column("route", sa.String(192), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "sheet_google_bindings",
        sa.Column("destination", sa.String(64), primary_key=True),
        sa.Column("route", sa.String(192), nullable=False),
    )
    op.create_table(
        "sheet_exports",
        sa.Column("route", sa.String(192), primary_key=True),
        sa.Column("target", sa.String(8), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("destination", sa.String(64)),
        sa.Column("request_id", sa.String(32), nullable=False),
        sa.Column("applied_id", sa.String(32)),
        sa.Column("applied_revision", sa.BigInteger()),
        sa.Column("file_identity", sa.String(64)),
        sa.Column("error", sa.String(300)),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("target in ('excel','google')", name="ck_export_target"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"""
            CREATE FUNCTION jarvis_touch_sheet_revision() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    {bump("OLD.route")}
                    RETURN OLD;
                END IF;
                IF TG_OP = 'UPDATE' AND NEW.route <> OLD.route THEN
                    {bump("OLD.route")}
                END IF;
                {bump("NEW.route")}
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        for table in TABLES:
            op.execute(f"""CREATE TRIGGER tr_sheet_{table} AFTER INSERT OR UPDATE OR DELETE
                ON {table} FOR EACH ROW EXECUTE FUNCTION jarvis_touch_sheet_revision()""")
    else:
        for table in TABLES:
            for event, owner in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
                op.execute(f"""CREATE TRIGGER tr_sheet_{table}_{event} AFTER {event} ON {table}
                    BEGIN {bump(owner + ".route")} END""")
            op.execute(f"""CREATE TRIGGER tr_sheet_{table}_move AFTER UPDATE OF route ON {table}
                WHEN NEW.route <> OLD.route BEGIN {bump("OLD.route")} END""")


def downgrade():
    for table in TABLES:
        if op.get_bind().dialect.name == "postgresql":
            op.execute(f"DROP TRIGGER tr_sheet_{table} ON {table}")
        else:
            for event in ("INSERT", "UPDATE", "DELETE", "move"):
                op.execute(f"DROP TRIGGER tr_sheet_{table}_{event}")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION jarvis_touch_sheet_revision()")
    op.drop_table("sheet_exports")
    op.drop_table("sheet_google_bindings")
    op.drop_table("sheet_revisions")
