"""Robô de publicação: uma execução por proposta aprovada (estado, passo, evidências, anúncio criado).

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-01 03:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "robot_runs",
        sa.Column("proposal_id", sa.UUID(), sa.ForeignKey("approval_proposals.id"), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("step", sa.String(64)),
        sa.Column("message", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("dropi_edit_url", sa.String(300)),
        sa.Column("listing_id", sa.String(32)),
        sa.Column("listing_url", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "state IN ('RUNNING','NEEDS_OWNER','FAILED','DONE','REHEARSED')", name="ck_robot_runs_state"
        ),
    )


def downgrade() -> None:
    op.drop_table("robot_runs")
