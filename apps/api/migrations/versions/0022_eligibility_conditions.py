"""Condições estruturadas e verificáveis da elegibilidade por canal (CONDITIONAL não é autorização irrestrita).

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-25 18:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "channel_eligibility",
        sa.Column("conditions", postgresql.JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("channel_eligibility", "conditions")
