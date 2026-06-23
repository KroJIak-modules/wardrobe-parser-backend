"""add dedup candidates and source dedup flag

Revision ID: 0059_add_dedup_candidates_and_source_dedup_flag
Revises: 0058_drop_designer_case_insensitive_uniques
Create Date: 2026-06-21 11:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0059_add_dedup_candidates_and_source_dedup_flag"
down_revision = "0058_drop_designer_case_insensitive_uniques"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _column_names(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)} if _table_exists(bind, table_name) else set()


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name)) if _table_exists(bind, table_name) else False


def _check_exists(bind, table_name: str, check_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(check.get("name") == check_name for check in inspector.get_check_constraints(table_name)) if _table_exists(bind, table_name) else False


def upgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "source_settings") and "dedup_enabled" not in _column_names(bind, "source_settings"):
        op.add_column(
            "source_settings",
            sa.Column("dedup_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        )

    if not _table_exists(bind, "product_dedup_candidates"):
        op.create_table(
            "product_dedup_candidates",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("left_product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
            sa.Column("right_product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
    if not _index_exists(bind, "product_dedup_candidates", "ix_product_dedup_candidates_left_product_id"):
        op.create_index("ix_product_dedup_candidates_left_product_id", "product_dedup_candidates", ["left_product_id"], unique=False)
    if not _index_exists(bind, "product_dedup_candidates", "ix_product_dedup_candidates_right_product_id"):
        op.create_index("ix_product_dedup_candidates_right_product_id", "product_dedup_candidates", ["right_product_id"], unique=False)
    if not _index_exists(bind, "product_dedup_candidates", "ux_product_dedup_candidates_pair"):
        op.create_index("ux_product_dedup_candidates_pair", "product_dedup_candidates", ["left_product_id", "right_product_id"], unique=True)
    if not _check_exists(bind, "product_dedup_candidates", "ck_product_dedup_candidates_left_lt_right"):
        op.create_check_constraint(
            "ck_product_dedup_candidates_left_lt_right",
            "product_dedup_candidates",
            "left_product_id < right_product_id",
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "product_dedup_candidates"):
        if _index_exists(bind, "product_dedup_candidates", "ux_product_dedup_candidates_pair"):
            op.drop_index("ux_product_dedup_candidates_pair", table_name="product_dedup_candidates")
        if _index_exists(bind, "product_dedup_candidates", "ix_product_dedup_candidates_right_product_id"):
            op.drop_index("ix_product_dedup_candidates_right_product_id", table_name="product_dedup_candidates")
        if _index_exists(bind, "product_dedup_candidates", "ix_product_dedup_candidates_left_product_id"):
            op.drop_index("ix_product_dedup_candidates_left_product_id", table_name="product_dedup_candidates")
        op.drop_table("product_dedup_candidates")

    if _table_exists(bind, "source_settings") and "dedup_enabled" in _column_names(bind, "source_settings"):
        op.drop_column("source_settings", "dedup_enabled")
