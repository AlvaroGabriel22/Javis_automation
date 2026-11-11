"""Identifica o provedor das agendas e protege o vínculo CalDAV do proprietário."""

import sqlalchemy as sa
from alembic import op

revision = "0008_icloud"
down_revision = "0007_calendar"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "calendar_sources",
        sa.Column("provider", sa.String(12), nullable=False, server_default="google"),
    )
    op.add_column("calendar_sources", sa.Column("remote_url", sa.Text()))
    op.create_table(
        "icloud_link",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account", sa.String(254), nullable=False),
        sa.Column("encrypted", sa.Text, nullable=False),
        sa.Column("error", sa.String(240)),
    )


def downgrade():
    # Os itens das origens iCloud ficam locais ao retornar à revisão anterior.
    op.execute(
        "UPDATE calendar_entries SET source_id=NULL, state='local' WHERE source_id IN (SELECT id FROM calendar_sources WHERE provider='icloud')"
    )
    op.execute("DELETE FROM calendar_sources WHERE provider='icloud'")
    op.drop_table("icloud_link")
    op.drop_column("calendar_sources", "remote_url")
    op.drop_column("calendar_sources", "provider")
