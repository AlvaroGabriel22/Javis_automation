"""Mantém identidades históricas e permite recadastrar um telefone com outro ID."""

from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op

revision = "0004_contact_removal"
down_revision = "0003_s02"
branch_labels = None
depends_on = None


@contextmanager
def replace_contacts_table():
    conn = op.get_bind()
    sqlite = conn.dialect.name == "sqlite"
    if sqlite:
        # SQLite recria a tabela para alterar UNIQUE. As tabelas filhas mantêm seus IDs.
        if not conn.connection.driver_connection.in_transaction:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        conn.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
    with op.batch_alter_table("contacts") as batch:
        yield batch
    if sqlite:
        if conn.exec_driver_sql("PRAGMA foreign_key_check").first():
            raise RuntimeError("Há referências inválidas após a migração de contatos.")
        conn.exec_driver_sql("PRAGMA defer_foreign_keys=OFF")


def upgrade():
    with replace_contacts_table() as batch:
        batch.drop_constraint("uq_contacts_phone", type_="unique")
        batch.add_column(
            sa.Column("removed", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    active = sa.column("removed").is_(False)
    op.create_index(
        "uq_active_contacts_phone",
        "contacts",
        ["owner_id", "phone"],
        unique=True,
        sqlite_where=active,
        postgresql_where=active,
    )


def downgrade():
    # A versão anterior tem uma única identidade por telefone, inclusive no histórico.
    if not op.get_context().as_sql:
        duplicates = sa.text(
            "SELECT 1 FROM contacts GROUP BY owner_id, phone HAVING COUNT(*) > 1 LIMIT 1"
        )
        if op.get_bind().execute(duplicates).first():
            raise RuntimeError(
                "O histórico contém telefones recadastrados. "
                "Para voltar à versão anterior, restaure o backup anterior à atualização."
            )
    with replace_contacts_table() as batch:
        batch.drop_index("uq_active_contacts_phone")
        batch.drop_column("removed")
        batch.create_unique_constraint("uq_contacts_phone", ["owner_id", "phone"])
