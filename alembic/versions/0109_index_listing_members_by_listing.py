"""index listing memberships for source cleanup

Revision ID: 0109_index_listing_members_by_listing
Revises: 0108_restore_enabled_designer_product_links
Create Date: 2026-08-25
"""

from __future__ import annotations

from alembic import op


revision = "0109_index_listing_members_by_listing"
down_revision = "0108_restore_enabled_designer_product_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_product_listing_members_listing_id", "product_listing_members", ["listing_id"])


def downgrade() -> None:
    op.drop_index("ix_product_listing_members_listing_id", table_name="product_listing_members")
