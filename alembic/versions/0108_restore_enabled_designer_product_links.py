"""restore links for enabled source-brand mappings

Revision ID: 0108_restore_enabled_designer_product_links
Revises: 0107_add_product_manual_visibility
Create Date: 2026-08-19
"""

from __future__ import annotations

from alembic import op


revision = "0108_restore_enabled_designer_product_links"
down_revision = "0107_add_product_manual_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        WITH restored_links AS (
            SELECT product.id, mapping.designer_id
            FROM products AS product
            JOIN product_listings AS listing ON listing.id = product.primary_listing_id
            LEFT JOIN product_presentation AS presentation ON presentation.product_id = product.id
            JOIN designer_source_names AS mapping
              ON lower(mapping.source_name) = lower(coalesce(presentation.brand_override_name, listing.source_designer_raw, ''))
            WHERE product.lifecycle_status = 'active'
              AND mapping.is_enabled IS TRUE
              AND mapping.designer_id IS NOT NULL
              AND product.designer_id IS NULL
        )
        UPDATE products AS product
        SET designer_id = restored_links.designer_id
        FROM restored_links
        WHERE product.id = restored_links.id
        """
    )


def downgrade() -> None:
    pass
