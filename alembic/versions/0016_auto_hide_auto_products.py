"""Add auto-hide flags for source/product visibility

Revision ID: 0016_auto_hide_auto_products
Revises: 0015_showcase_media_settings
Create Date: 2026-04-21 05:45:00.000000
"""

from alembic import op


revision = "0016_auto_hide_auto_products"
down_revision = "0015_showcase_media_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE parser_source "
        "ADD COLUMN IF NOT EXISTS hide_auto_added_products BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE parser_product "
        "ADD COLUMN IF NOT EXISTS is_auto_added BOOLEAN NOT NULL DEFAULT TRUE"
    )
    op.execute(
        "ALTER TABLE parser_product "
        "ADD COLUMN IF NOT EXISTS auto_hide_force_visible BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE parser_product DROP COLUMN IF EXISTS auto_hide_force_visible")
    op.execute("ALTER TABLE parser_product DROP COLUMN IF EXISTS is_auto_added")
    op.execute("ALTER TABLE parser_source DROP COLUMN IF EXISTS hide_auto_added_products")
