"""Registro de chamadas de pesquisa externa (teto, intervalo e auditoria; só acréscimo, sem chave nem consulta crua).

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-26 02:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_calls",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("query_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("http_status", sa.Integer()),
        sa.Column("results", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('OK','ERROR','BLOCKED')", name="ck_research_calls_status"),
    )
    op.create_index("ix_research_calls_provider_at", "research_calls", ["provider", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_research_calls_provider_at", table_name="research_calls")
    op.drop_table("research_calls")
