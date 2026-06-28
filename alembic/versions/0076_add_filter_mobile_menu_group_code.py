"""add filter mobile menu group code

Revision ID: 0076_add_filter_mobile_menu_group_code
Revises: 0075_add_product_listing_variant_pricing_mode
Create Date: 2026-06-28 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "0076_add_filter_mobile_menu_group_code"
down_revision = "0075_add_product_listing_variant_pricing_mode"
branch_labels = None
depends_on = None


def _column_exists(bind: Connection, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "filters", "mobile_menu_group_code"):
        op.add_column("filters", sa.Column("mobile_menu_group_code", sa.String(length=64), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, "filters", "mobile_menu_group_code"):
        op.drop_column("filters", "mobile_menu_group_code")
