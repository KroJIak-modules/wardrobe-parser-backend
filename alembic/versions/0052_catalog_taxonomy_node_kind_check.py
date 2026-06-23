"""catalog taxonomy node_kind check

Revision ID: 0052_catalog_taxonomy_node_kind_check
Revises: 0051_drop_backend_legacy_tables
Create Date: 2026-06-20 03:10:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0052_catalog_taxonomy_node_kind_check"
down_revision = "0051_drop_backend_legacy_tables"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _check_exists(bind, table_name: str, check_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(constraint.get("name") == check_name for constraint in inspector.get_check_constraints(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "filters") and not _check_exists(bind, "filters", "ck_filters_node_kind"):
        op.create_check_constraint(
            "ck_filters_node_kind",
            "filters",
            "node_kind IN ('filter', 'multifilter')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "filters") and _check_exists(bind, "filters", "ck_filters_node_kind"):
        op.drop_constraint("ck_filters_node_kind", "filters", type_="check")
