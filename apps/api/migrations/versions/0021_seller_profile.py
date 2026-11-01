"""Perfil do vendedor (Pessoa Física/Jurídica) e matriz de elegibilidade por canal (ADR 0006). Não guarda CPF/CNPJ.
Registros existentes são preservados; o perfil padrão é PERSON (decisão do proprietário).

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-25 12:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = sa.text("now()")
    op.create_table(
        "seller_profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("person_type", sa.String(16), nullable=False, server_default="PERSON"),
        sa.Column("country", sa.String(2), nullable=False, server_default="BR"),
        sa.Column("uf", sa.String(2)),
        sa.Column("tax_id_status", sa.String(24), nullable=False, server_default="NOT_COLLECTED"),
        sa.Column("fiscal_model", sa.String(32), nullable=False, server_default="UNDEFINED"),
        sa.Column("restrictions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("verified_source", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.CheckConstraint("id = 1", name="ck_seller_profile_single_row"),
        sa.CheckConstraint("person_type IN ('PERSON','COMPANY')", name="ck_seller_profile_person_type"),
    )
    op.create_table(
        "channel_eligibility",
        sa.Column("channel", sa.String(32), primary_key=True),
        sa.Column("seller_type", sa.String(16), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="UNVERIFIED"),
        sa.Column("aspects", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("restrictions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("pending", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("sources", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("scope", sa.Text(), nullable=False, server_default=""),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.CheckConstraint(
            "status IN ('SUPPORTED','CONDITIONAL','UNVERIFIED','UNSUPPORTED')", name="ck_channel_eligibility_status"
        ),
    )
    op.execute("INSERT INTO seller_profile (id, person_type) VALUES (1, 'PERSON')")


def downgrade() -> None:
    op.drop_table("channel_eligibility")
    op.drop_table("seller_profile")
