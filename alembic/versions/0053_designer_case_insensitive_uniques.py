"""designer case-insensitive unique indexes

Revision ID: 0053_designer_case_insensitive_uniques
Revises: 0052_catalog_taxonomy_node_kind_check
Create Date: 2026-06-20 03:20:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0053_designer_case_insensitive_uniques"
down_revision = "0052_catalog_taxonomy_node_kind_check"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "designers") and not _index_exists(bind, "designers", "uq_designers_lower_name"):
        op.create_index("uq_designers_lower_name", "designers", [sa.text("lower(name)")], unique=True)
    if _table_exists(bind, "designer_source_names") and not _index_exists(bind, "designer_source_names", "uq_designer_source_names_lower_source_name"):
        op.create_index(
            "uq_designer_source_names_lower_source_name",
            "designer_source_names",
            [sa.text("lower(source_name)")],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "designer_source_names") and _index_exists(bind, "designer_source_names", "uq_designer_source_names_lower_source_name"):
        op.drop_index("uq_designer_source_names_lower_source_name", table_name="designer_source_names")
    if _table_exists(bind, "designers") and _index_exists(bind, "designers", "uq_designers_lower_name"):
        op.drop_index("uq_designers_lower_name", table_name="designers")
