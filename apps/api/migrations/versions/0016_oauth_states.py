"""oauth_states: verificador PKCE no servidor para conectar contas reais de canal (Mercado Livre)

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-24 22:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_states",
        sa.Column("state_sha256", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("code_verifier_ciphertext", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.String(length=500), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state_sha256"),
    )


def downgrade() -> None:
    op.drop_table("oauth_states")
