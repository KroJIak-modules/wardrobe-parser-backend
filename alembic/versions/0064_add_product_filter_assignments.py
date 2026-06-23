"""add product filter assignments

Revision ID: 0064_add_product_filter_assignments
Revises: 0063_add_product_listing_source_tags
Create Date: 2026-06-21 22:50:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection


revision = "0064_add_product_filter_assignments"
down_revision = "0063_add_product_listing_source_tags"
branch_labels = None
depends_on = None


def _table_names(bind: Connection) -> set[str]:
    return set(inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)

    if "product_filter_assignments" not in tables:
        op.create_table(
            "product_filter_assignments",
            sa.Column("product_id", sa.BigInteger(), nullable=False),
            sa.Column("revision", sa.BigInteger(), nullable=False),
            sa.Column("filter_slug", sa.String(length=255), nullable=False),
            sa.Column("filter_label", sa.Text(), nullable=False),
            sa.Column("manual_rank", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("match_score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "matched_local_keywords",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "matched_title_keywords",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("revision > 0", name="ck_product_filter_assignments_revision_positive"),
            sa.CheckConstraint("manual_rank IN (0, 1)", name="ck_product_filter_assignments_manual_rank"),
            sa.CheckConstraint("match_score >= 0", name="ck_product_filter_assignments_match_score_non_negative"),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("product_id", "revision"),
        )
        op.create_index(
            "idx_product_filter_assignments_revision_slug",
            "product_filter_assignments",
            ["revision", "filter_slug", "product_id"],
            unique=False,
        )
        op.create_index(
            "idx_product_filter_assignments_revision_product",
            "product_filter_assignments",
            ["revision", "product_id"],
            unique=False,
        )

    if "filter_assignment_runtime_state" not in tables:
        op.create_table(
            "filter_assignment_runtime_state",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("target_revision", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("applied_revision", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("rebuild_requested_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rebuild_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rebuild_completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("target_revision >= 0", name="ck_filter_assignment_runtime_state_target_non_negative"),
            sa.CheckConstraint("applied_revision >= 0", name="ck_filter_assignment_runtime_state_applied_non_negative"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.execute(
            sa.text(
                """
                insert into filter_assignment_runtime_state (id, target_revision, applied_revision, rebuild_requested_at)
                values (1, 1, 0, now())
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)
    if "filter_assignment_runtime_state" in tables:
        op.drop_table("filter_assignment_runtime_state")
    if "product_filter_assignments" in tables:
        op.drop_index("idx_product_filter_assignments_revision_product", table_name="product_filter_assignments")
        op.drop_index("idx_product_filter_assignments_revision_slug", table_name="product_filter_assignments")
        op.drop_table("product_filter_assignments")
