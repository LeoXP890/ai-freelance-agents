"""Rotinas dos agentes: tarefas recorrentes (diária em horário fixo ou a cada N horas) disparadas pelo worker.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-31 20:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_routines",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("agent_id", sa.String(32), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("input", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("schedule", sa.String(16), nullable=False),
        sa.Column("time_of_day", sa.String(5)),
        sa.Column("interval_hours", sa.Integer()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_task_id", sa.UUID()),
        sa.Column("last_outcome", sa.String(32)),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("schedule IN ('DAILY','INTERVAL')", name="ck_routine_schedule"),
        sa.CheckConstraint(
            "(schedule = 'DAILY' AND time_of_day ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' AND interval_hours IS NULL)"
            " OR (schedule = 'INTERVAL' AND interval_hours BETWEEN 2 AND 168 AND time_of_day IS NULL)",
            name="ck_routine_schedule_fields",
        ),
    )
    op.create_index("ix_agent_routines_due", "agent_routines", ["enabled", "next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_routines_due", table_name="agent_routines")
    op.drop_table("agent_routines")
