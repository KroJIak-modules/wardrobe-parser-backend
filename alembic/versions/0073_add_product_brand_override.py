"""add product brand override

Revision ID: 0073_add_product_brand_override
Revises: 0072_add_product_source_gender
Create Date: 2026-06-25 09:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0073_add_product_brand_override"
down_revision = "0072_add_product_source_gender"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "product_presentation") and not _has_column(bind, "product_presentation", "brand_override_name"):
        op.add_column("product_presentation", sa.Column("brand_override_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "product_presentation") and _has_column(bind, "product_presentation", "brand_override_name"):
        op.drop_column("product_presentation", "brand_override_name")
