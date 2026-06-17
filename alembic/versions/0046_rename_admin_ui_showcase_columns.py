"""rename admin ui showcase columns

Revision ID: 0046_rename_admin_ui_showcase_columns
Revises: 0045_create_catalog_v2_taxonomy
Create Date: 2026-06-17 16:20:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0046_rename_admin_ui_showcase_columns"
down_revision = "0045_create_catalog_v2_taxonomy"
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "admin_ui_settings", "showcase_hero_image_asset_id") and not _has_column(bind, "admin_ui_settings", "hero_image_asset_id"):
        op.alter_column("admin_ui_settings", "showcase_hero_image_asset_id", new_column_name="hero_image_asset_id")
    if _has_column(bind, "admin_ui_settings", "showcase_carousel_image_asset_ids") and not _has_column(bind, "admin_ui_settings", "carousel_image_asset_ids"):
        op.alter_column("admin_ui_settings", "showcase_carousel_image_asset_ids", new_column_name="carousel_image_asset_ids")


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "admin_ui_settings", "hero_image_asset_id") and not _has_column(bind, "admin_ui_settings", "showcase_hero_image_asset_id"):
        op.alter_column("admin_ui_settings", "hero_image_asset_id", new_column_name="showcase_hero_image_asset_id")
    if _has_column(bind, "admin_ui_settings", "carousel_image_asset_ids") and not _has_column(bind, "admin_ui_settings", "showcase_carousel_image_asset_ids"):
        op.alter_column("admin_ui_settings", "carousel_image_asset_ids", new_column_name="showcase_carousel_image_asset_ids")
