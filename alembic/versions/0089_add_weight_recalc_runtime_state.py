"""Add persistent runtime state for weight recalculation.

Revision ID: 0089_add_weight_recalc_runtime_state
Revises: 0088_add_filter_default_weight_rule
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "0089_add_weight_recalc_runtime_state"
down_revision = "0088_add_filter_default_weight_rule"
branch_labels = None
depends_on = None


def _table_exists(bind: Connection, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "weight_recalc_runtime_state"):
        return
    op.create_table(
        "weight_recalc_runtime_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="idle"),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("total_products", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("processed_products", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('idle', 'queued', 'running')", name="ck_weight_recalc_runtime_state_status"),
        sa.CheckConstraint("total_products >= 0", name="ck_weight_recalc_runtime_state_total_non_negative"),
        sa.CheckConstraint("processed_products >= 0", name="ck_weight_recalc_runtime_state_processed_non_negative"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "weight_recalc_runtime_state"):
        return
    op.drop_table("weight_recalc_runtime_state")
