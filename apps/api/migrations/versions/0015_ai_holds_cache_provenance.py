"""ai_spend_holds + procedência no llm_cache (auditoria A02/A03)

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-24 18:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reserva do pior custo de uma chamada de IA ANTES de chamar (teto mensal atômico); expira se o processo cair.
    op.create_table(
        "ai_spend_holds",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.String(length=32), nullable=False),
        sa.Column("amount_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_micro_usd >= 0", name="ck_ai_hold_nonneg"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_spend_holds_expires", "ai_spend_holds", ["expires_at"])
    # Cache nunca serve resposta de um provedor/ambiente para outro (mock ≠ real)
    op.add_column("llm_cache", sa.Column("provider", sa.String(length=32), nullable=False, server_default="mock"))
    op.add_column("llm_cache", sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.execute("DELETE FROM llm_cache")  # entradas antigas não têm procedência confiável


def downgrade() -> None:
    op.drop_column("llm_cache", "simulated")
    op.drop_column("llm_cache", "provider")
    op.drop_index("ix_ai_spend_holds_expires", table_name="ai_spend_holds")
    op.drop_table("ai_spend_holds")
