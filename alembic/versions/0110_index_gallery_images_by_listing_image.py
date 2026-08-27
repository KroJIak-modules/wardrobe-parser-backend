"""index gallery references during source cleanup

Revision ID: 0110_index_gallery_images_by_listing_image
Revises: 0109_index_listing_members_by_listing
Create Date: 2026-08-25
"""

from __future__ import annotations

from alembic import op


revision = "0110_index_gallery_images_by_listing_image"
down_revision = "0109_index_listing_members_by_listing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_product_listing_gallery_images_listing_image_id",
        "product_listing_gallery_images",
        ["listing_image_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_listing_gallery_images_listing_image_id",
        table_name="product_listing_gallery_images",
    )
