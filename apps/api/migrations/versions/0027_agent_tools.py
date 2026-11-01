"""Ferramentas dos agentes: cache de resultados (com validade) e registro de cada chamada (auditoria e custo).

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-31 22:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tool_cache_expires", "tool_cache", ["expires_at"])
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("agent_id", sa.String(32), nullable=False),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("args", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(200)),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("task_id", sa.UUID()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('OK','ERROR','DENIED','LIMIT')", name="ck_tool_calls_status"),
    )
    op.create_index("ix_tool_calls_agent_tool_created", "tool_calls", ["agent_id", "tool", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_tool_calls_agent_tool_created", table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_index("ix_tool_cache_expires", table_name="tool_cache")
    op.drop_table("tool_cache")
