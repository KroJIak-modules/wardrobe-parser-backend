"""add filter assignment rebuild progress

Revision ID: 0098_add_filter_assignment_rebuild_progress
Revises: 0097_add_filter_restrict_by_gender
Create Date: 2026-07-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0098_add_filter_assignment_rebuild_progress"
down_revision = "0097_add_filter_restrict_by_gender"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "filter_assignment_runtime_state",
        sa.Column("rebuild_total_products", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "filter_assignment_runtime_state",
        sa.Column("rebuild_processed_products", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_filter_assignment_runtime_state_total_non_negative",
        "filter_assignment_runtime_state",
        "rebuild_total_products >= 0",
    )
    op.create_check_constraint(
        "ck_filter_assignment_runtime_state_processed_non_negative",
        "filter_assignment_runtime_state",
        "rebuild_processed_products >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_filter_assignment_runtime_state_processed_non_negative",
        "filter_assignment_runtime_state",
        type_="check",
    )
    op.drop_constraint(
        "ck_filter_assignment_runtime_state_total_non_negative",
        "filter_assignment_runtime_state",
        type_="check",
    )
    op.drop_column("filter_assignment_runtime_state", "rebuild_processed_products")
    op.drop_column("filter_assignment_runtime_state", "rebuild_total_products")
