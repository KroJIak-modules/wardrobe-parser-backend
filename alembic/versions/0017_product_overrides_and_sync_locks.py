"""Add product overrides and per-field sync locks

Revision ID: 0017_product_overrides_and_sync_locks
Revises: 0016_auto_hide_auto_products
Create Date: 2026-04-22 12:20:00.000000
"""

from alembic import op


revision = "0017_product_overrides_and_sync_locks"
down_revision = "0016_auto_hide_auto_products"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE parser_product ADD COLUMN IF NOT EXISTS title_override TEXT")
    op.execute("ALTER TABLE parser_product ADD COLUMN IF NOT EXISTS description_override TEXT")
    op.execute("ALTER TABLE parser_product ADD COLUMN IF NOT EXISTS title_sync_locked BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE parser_product ADD COLUMN IF NOT EXISTS description_sync_locked BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE parser_product ADD COLUMN IF NOT EXISTS images_sync_locked BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE parser_product ADD COLUMN IF NOT EXISTS hidden_source_image_asset_ids JSON NOT NULL DEFAULT '[]'::json")
    op.execute("ALTER TABLE parser_product ADD COLUMN IF NOT EXISTS manual_image_asset_ids JSON NOT NULL DEFAULT '[]'::json")
    op.execute("ALTER TABLE parser_product ADD COLUMN IF NOT EXISTS manual_image_order JSON NOT NULL DEFAULT '[]'::json")


def downgrade() -> None:
    op.execute("ALTER TABLE parser_product DROP COLUMN IF EXISTS manual_image_order")
    op.execute("ALTER TABLE parser_product DROP COLUMN IF EXISTS manual_image_asset_ids")
    op.execute("ALTER TABLE parser_product DROP COLUMN IF EXISTS hidden_source_image_asset_ids")
    op.execute("ALTER TABLE parser_product DROP COLUMN IF EXISTS images_sync_locked")
    op.execute("ALTER TABLE parser_product DROP COLUMN IF EXISTS description_sync_locked")
    op.execute("ALTER TABLE parser_product DROP COLUMN IF EXISTS title_sync_locked")
    op.execute("ALTER TABLE parser_product DROP COLUMN IF EXISTS description_override")
    op.execute("ALTER TABLE parser_product DROP COLUMN IF EXISTS title_override")
