"""media_blobs: fotos de produto enviadas pelo app (JPEG sem metadados, endereço = SHA-256)

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-24 23:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_blobs",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 10485760", name="ck_media_size"),
        sa.PrimaryKeyConstraint("sha256"),
    )


def downgrade() -> None:
    op.drop_table("media_blobs")
