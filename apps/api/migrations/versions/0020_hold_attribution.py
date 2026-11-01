"""ai_spend_holds com atribuição (tarefa, missão, finalidade, provedor): reserva expirada após queda vira custo

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-25 02:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_spend_holds", sa.Column("task_id", sa.UUID(), nullable=True))
    op.add_column("ai_spend_holds", sa.Column("mission_id", sa.UUID(), nullable=True))
    op.add_column("ai_spend_holds", sa.Column("purpose", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("ai_spend_holds", sa.Column("provider", sa.String(length=32), nullable=False, server_default=""))
    op.add_column("ai_spend_holds", sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    for c in ("simulated", "provider", "purpose", "mission_id", "task_id"):
        op.drop_column("ai_spend_holds", c)
