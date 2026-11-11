"""Guarda recursos incompatíveis que aguardam outra versão no Google."""

import sqlalchemy as sa
from alembic import op

revision = "0012_calendar_issues"
down_revision = "0011_calendar_choice"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "calendar_import_issues",
        sa.Column(
            "source_id",
            sa.String(512),
            sa.ForeignKey("calendar_sources.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("remote_id", sa.String(1024), primary_key=True),
        sa.Column("etag", sa.String(256)),
    )


def downgrade():
    op.drop_table("calendar_import_issues")
