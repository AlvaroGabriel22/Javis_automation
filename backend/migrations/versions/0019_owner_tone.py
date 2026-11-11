"""Tom de conversa escolhido no perfil do proprietário."""

import sqlalchemy as sa
from alembic import op

revision = "0019_owner_tone"
down_revision = "0018_sticker_preference"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "owners",
        sa.Column(
            "tone",
            sa.String(7),
            sa.CheckConstraint("tone IN ('proximo', 'neutro')"),
            nullable=False,
            server_default="proximo",
        ),
    )


def downgrade():
    op.drop_column("owners", "tone")
