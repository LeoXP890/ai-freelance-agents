"""Caixa para pagamento ao fornecedor: saldo liberado primeiro, fundo de reserva depois (ADR 0008, DRY_RUN).

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-31 10:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

APPEND_ONLY = ("cash_balance_snapshots", "funding_legs", "funding_events")
LIVE = "status IN ('RESERVED','PAYMENT_PENDING','PAYMENT_UNKNOWN','PAID')"


def _now():
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    op.create_table(
        "cash_sources",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("is_simulated", sa.Boolean(), nullable=False),
        sa.Column("floor_minor", sa.BigInteger()),
        sa.Column("per_order_limit_minor", sa.BigInteger()),
        sa.Column("period_limit_minor", sa.BigInteger()),
        sa.Column("period_days", sa.Integer()),
        sa.Column("max_exposure_minor", sa.BigInteger()),
        sa.Column("policy_approved_by", sa.String(128)),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        _now(),
        sa.CheckConstraint("kind IN ('OPERATING','RESERVE')", name="ck_cash_source_kind"),
        sa.CheckConstraint("floor_minor IS NULL OR floor_minor >= 0", name="ck_cash_source_floor"),
        sa.CheckConstraint("per_order_limit_minor IS NULL OR per_order_limit_minor > 0", name="ck_cash_source_order"),
        sa.CheckConstraint("period_limit_minor IS NULL OR period_limit_minor > 0", name="ck_cash_source_period"),
        sa.CheckConstraint("period_days IS NULL OR period_days > 0", name="ck_cash_source_days"),
        sa.CheckConstraint("max_exposure_minor IS NULL OR max_exposure_minor > 0", name="ck_cash_source_exposure"),
    )
    op.create_table(
        "cash_balance_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("source_id", sa.UUID(), sa.ForeignKey("cash_sources.id"), nullable=False),
        sa.Column("available_minor", sa.BigInteger(), nullable=False),
        sa.Column("retained_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("external_ref", sa.String(128)),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("recorded_by", sa.String(128), nullable=False),
        _now(),
        sa.CheckConstraint("available_minor >= 0", name="ck_cash_snapshot_available"),
        sa.CheckConstraint("retained_minor >= 0", name="ck_cash_snapshot_retained"),
    )
    op.create_index("ix_cash_snapshot_source_observed", "cash_balance_snapshots", ["source_id", "observed_at"])
    op.create_table(
        "funding_allocations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("obligation_key", sa.String(160), nullable=False),
        sa.Column("order_id", sa.UUID(), sa.ForeignKey("orders.id")),
        sa.Column("supplier_id", sa.UUID(), sa.ForeignKey("suppliers.id")),
        sa.Column("proposal_id", sa.UUID(), sa.ForeignKey("approval_proposals.id")),
        sa.Column("quote_ref", sa.String(128), nullable=False),
        sa.Column("quote_valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("beneficiary_ref", sa.String(128), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("block_reason", sa.String(48)),
        sa.Column("payment_ref", sa.String(128)),
        sa.Column("payment_attempts", sa.Integer(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("reserve_recovered_minor", sa.BigInteger(), nullable=False),
        sa.Column("is_simulated", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        _now(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("amount_minor > 0", name="ck_funding_amount_pos"),
        sa.CheckConstraint(
            "status IN ('BLOCKED','RESERVED','PAYMENT_PENDING','PAYMENT_UNKNOWN','PAID','FAILED','RELEASED')",
            name="ck_funding_status",
        ),
        sa.CheckConstraint("reserve_recovered_minor >= 0", name="ck_funding_recovered"),
    )
    # Garantia no banco: nunca duas alocações vivas (logo, nunca dois pagamentos) para a mesma obrigação.
    op.create_index(
        "uq_funding_live_obligation",
        "funding_allocations",
        ["obligation_key"],
        unique=True,
        postgresql_where=sa.text(LIVE),
    )
    op.create_index("ix_funding_status", "funding_allocations", ["status"])
    op.create_table(
        "funding_legs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("allocation_id", sa.UUID(), sa.ForeignKey("funding_allocations.id"), nullable=False),
        sa.Column("source_id", sa.UUID(), sa.ForeignKey("cash_sources.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), sa.ForeignKey("cash_balance_snapshots.id"), nullable=False),
        _now(),
        sa.CheckConstraint("amount_minor > 0", name="ck_funding_leg_pos"),
        sa.UniqueConstraint("allocation_id", "source_id"),
    )
    op.create_table(
        "funding_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("allocation_id", sa.UUID(), sa.ForeignKey("funding_allocations.id"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("amount_minor", sa.BigInteger()),
        sa.Column("external_ref", sa.String(128)),
        sa.Column("idempotency_key", sa.String(180), nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        _now(),
    )
    op.create_index("ix_funding_events_allocation", "funding_events", ["allocation_id"])
    for table in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION aic_forbid_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION aic_forbid_mutation()"
        )


def downgrade() -> None:
    for table in ("funding_events", "funding_legs", "funding_allocations", "cash_balance_snapshots", "cash_sources"):
        op.drop_table(table)
