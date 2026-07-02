"""Add cached site sort price to products.

Revision ID: 0084_add_site_sort_price_cache
Revises: 0083_sale_category_title_ru
Create Date: 2026-07-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0084_add_site_sort_price_cache"
down_revision = "0083_sale_category_title_ru"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("site_sort_price_rub", sa.Numeric(12, 2), nullable=True))
    op.add_column("products", sa.Column("site_sort_price_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_products_site_sort_price_rub", "products", ["site_sort_price_rub"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_products_site_sort_price_rub", table_name="products")
    op.drop_column("products", "site_sort_price_synced_at")
    op.drop_column("products", "site_sort_price_rub")
