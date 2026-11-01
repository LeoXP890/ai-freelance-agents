"""desktop_releases + desktop_artifacts: atualização automática do app desktop servida pela própria API

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-25 00:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "desktop_releases",
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("published_by", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("version"),
    )
    op.create_table(
        "desktop_artifacts",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 157286400", name="ck_desktop_artifact_size"),
        sa.ForeignKeyConstraint(["version"], ["desktop_releases.version"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sha256"),
        sa.UniqueConstraint("version", "platform"),
    )


def downgrade() -> None:
    op.drop_table("desktop_artifacts")
    op.drop_table("desktop_releases")
