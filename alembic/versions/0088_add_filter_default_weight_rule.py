"""Add filter default weight rule mapping.

Revision ID: 0088_add_filter_default_weight_rule
Revises: 0087_fix_public_catalog_filter_keywords
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "0088_add_filter_default_weight_rule"
down_revision = "0087_fix_public_catalog_filter_keywords"
branch_labels = None
depends_on = None


def _table_exists(bind: Connection, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(bind: Connection, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(str(column.get("name") or "") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "filters") or not _table_exists(bind, "weight_rules"):
        return
    if not _column_exists(bind, "filters", "default_weight_rule_id"):
        op.add_column("filters", sa.Column("default_weight_rule_id", sa.BigInteger(), nullable=True))
    op.create_index(
        "ix_filters_default_weight_rule_id",
        "filters",
        ["default_weight_rule_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_filters_default_weight_rule_id_weight_rules",
        "filters",
        "weight_rules",
        ["default_weight_rule_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "filters"):
        return
    if _column_exists(bind, "filters", "default_weight_rule_id"):
        op.drop_constraint("fk_filters_default_weight_rule_id_weight_rules", "filters", type_="foreignkey")
        op.drop_index("ix_filters_default_weight_rule_id", table_name="filters", if_exists=True)
        op.drop_column("filters", "default_weight_rule_id")
