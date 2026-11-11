"""Momento do pedido para atribuir recados ao dono no horário real do envio."""

import sqlalchemy as sa
from alembic import op

revision = "0005_voice_relay"
down_revision = "0004_contact_removal"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("schedules", sa.Column("relay_requested_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("schedules", "relay_requested_at")
