"""add source published at to product listings

Revision ID: 0095_add_source_published_at_to_product_listings
Revises: 0094_restore_pricing_conversion_coefficients
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0095_add_source_published_at_to_product_listings"
down_revision = "0094_restore_pricing_conversion_coefficients"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_listings", sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("idx_product_listings_source_published_at", "product_listings", ["source_published_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_product_listings_source_published_at", table_name="product_listings")
    op.drop_column("product_listings", "source_published_at")
