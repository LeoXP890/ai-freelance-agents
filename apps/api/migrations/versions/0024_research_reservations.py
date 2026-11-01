"""Reserve external research quota before network I/O.

Revision ID: 0024
Revises: 0023
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_research_calls_status", "research_calls", type_="check")
    op.create_check_constraint(
        "ck_research_calls_status", "research_calls", "status IN ('OK','ERROR','BLOCKED','IN_FLIGHT')"
    )


def downgrade():
    # Unknown outcomes must still consume quota after rollback, never turn into BLOCKED/refunded calls.
    op.execute(
        "UPDATE research_calls SET status='ERROR', detail='Interrupted reservation; outcome unknown' "
        "WHERE status='IN_FLIGHT'"
    )
    op.drop_constraint("ck_research_calls_status", "research_calls", type_="check")
    op.create_check_constraint("ck_research_calls_status", "research_calls", "status IN ('OK','ERROR','BLOCKED')")
