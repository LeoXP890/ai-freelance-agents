"""kb_chunks.embedder: de qual embedder veio cada vetor (busca nunca compara espaços vetoriais diferentes)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-25 01:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kb_chunks", sa.Column("embedder", sa.String(length=64), nullable=False, server_default="hashing-v1"))
    op.create_index("ix_kb_chunks_embedder", "kb_chunks", ["embedder"])


def downgrade() -> None:
    op.drop_index("ix_kb_chunks_embedder", table_name="kb_chunks")
    op.drop_column("kb_chunks", "embedder")
