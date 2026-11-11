"""Voz, catálogo do aparelho e despacho durável de agendamentos."""

import sqlalchemy as sa
from alembic import op

revision = "0003_s02"
down_revision = "0002_s01"
branch_labels = None
depends_on = None


def upgrade():
    instant = sa.DateTime(timezone=True)
    op.add_column("contacts", sa.Column("source_route", sa.String(192)))
    op.add_column(
        "wa_inbox", sa.Column("kind", sa.String(10), nullable=False, server_default="text")
    )
    op.add_column("wa_inbox", sa.Column("media", sa.LargeBinary()))
    with op.batch_alter_table("schedules") as batch:
        batch.drop_constraint("ck_schedule_status", type_="check")
        batch.create_check_constraint(
            "ck_schedule_status",
            "status IN ('scheduled','cancelled','sending','accepted','needs_review','uncertain','failed')",
        )
        batch.add_column(sa.Column("route_key", sa.String(192)))
        batch.add_column(
            sa.Column("recurrence", sa.String(12), nullable=False, server_default="none")
        )
        batch.add_column(sa.Column("retry_at", instant))
        batch.add_column(sa.Column("active_attempt", sa.Uuid()))
        batch.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("result_detail", sa.String(120)))
    op.create_index("ix_schedules_due", "schedules", ["route_key", "status", "scheduled_at"])
    op.create_table(
        "contact_aliases",
        sa.Column("contact_id", sa.Uuid(), sa.ForeignKey("contacts.id"), primary_key=True),
        sa.Column("alias", sa.String(120), primary_key=True),
    )
    op.create_table(
        "device_contacts",
        sa.Column("route_key", sa.String(192), primary_key=True),
        sa.Column("phone", sa.String(16), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
    )
    op.create_table(
        "schedule_attempts",
        sa.Column("schedule_id", sa.Uuid(), sa.ForeignKey("schedules.id"), primary_key=True),
        sa.Column("token", sa.Uuid(), primary_key=True),
        sa.Column("started_at", instant, nullable=False),
        sa.Column("ended_at", instant),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("external_id", sa.String(192)),
    )


def downgrade():
    # A S01 não representa recibos e tentativas. Impede reativar resultados históricos como novos envios.
    for table in ("schedule_attempts", "device_contacts", "contact_aliases"):
        op.drop_table(table)
    op.execute("UPDATE schedules SET status = 'cancelled' WHERE status != 'scheduled'")
    op.drop_index("ix_schedules_due", table_name="schedules")
    with op.batch_alter_table("schedules") as batch:
        batch.drop_constraint("ck_schedule_status", type_="check")
        batch.create_check_constraint("ck_schedule_status", "status IN ('scheduled','cancelled')")
        for name in (
            "active_attempt",
            "attempts",
            "result_detail",
            "retry_at",
            "recurrence",
            "route_key",
        ):
            batch.drop_column(name)
    for name in ("media", "kind"):
        op.drop_column("wa_inbox", name)
    op.drop_column("contacts", "source_route")
