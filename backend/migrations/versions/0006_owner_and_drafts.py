"""Preferências do dono e pedidos de envio aguardando resposta no WhatsApp."""

import sqlalchemy as sa
from alembic import op

revision = "0006_owner_and_drafts"
down_revision = "0005_voice_relay"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("owners", sa.Column("preferred_name", sa.String(120)))
    op.add_column(
        "owners", sa.Column("gender", sa.String(40), nullable=False, server_default="Não informado")
    )
    op.add_column(
        "owners", sa.Column("address_as", sa.String(40), nullable=False, server_default="senhor")
    )
    op.create_table(
        "message_drafts",
        sa.Column("route_key", sa.String(192), primary_key=True),
        sa.Column("contact_name", sa.String(120), nullable=False),
        sa.Column("phone", sa.String(16)),
        sa.Column("choices", sa.JSON()),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("immediate", sa.Boolean(), nullable=False),
    )


def downgrade():
    op.drop_table("message_drafts")
    for column in ("address_as", "gender", "preferred_name"):
        op.drop_column("owners", column)
