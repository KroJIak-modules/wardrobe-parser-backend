"""split admin ui settings from pricing settings

Revision ID: 0020_admin_ui_settings_split
Revises: 0019_brand_mapping_include_in_designers
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_admin_ui_settings_split"
down_revision = "0019_brand_mapping_include_in_designers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_ui_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("designers_min_products", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("designers_exclude_store_vendors", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("showcase_hero_image_asset_id", sa.Integer(), nullable=True),
        sa.Column("showcase_carousel_image_asset_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_admin_ui_settings_updated_at", "admin_ui_settings", ["updated_at"], unique=False)
    op.execute(
        """
        INSERT INTO admin_ui_settings (
            id,
            designers_min_products,
            designers_exclude_store_vendors,
            showcase_hero_image_asset_id,
            showcase_carousel_image_asset_ids
        )
        SELECT
            1,
            COALESCE(designers_min_products, 1),
            COALESCE(designers_exclude_store_vendors, false),
            showcase_hero_image_asset_id,
            COALESCE(showcase_carousel_image_asset_ids, '[]'::json)
        FROM parser_pricing_settings
        ORDER BY id
        LIMIT 1
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO admin_ui_settings (id, designers_min_products, designers_exclude_store_vendors, showcase_carousel_image_asset_ids)
        SELECT 1, 1, false, '[]'::json
        WHERE NOT EXISTS (SELECT 1 FROM admin_ui_settings WHERE id = 1)
        """
    )
    op.drop_column("parser_pricing_settings", "designers_min_products")
    op.drop_column("parser_pricing_settings", "designers_exclude_store_vendors")
    op.drop_column("parser_pricing_settings", "showcase_hero_image_asset_id")
    op.drop_column("parser_pricing_settings", "showcase_carousel_image_asset_ids")


def downgrade() -> None:
    op.add_column("parser_pricing_settings", sa.Column("designers_min_products", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("parser_pricing_settings", sa.Column("designers_exclude_store_vendors", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("parser_pricing_settings", sa.Column("showcase_hero_image_asset_id", sa.Integer(), nullable=True))
    op.add_column("parser_pricing_settings", sa.Column("showcase_carousel_image_asset_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")))
    op.execute(
        """
        UPDATE parser_pricing_settings p
        SET
            designers_min_products = a.designers_min_products,
            designers_exclude_store_vendors = a.designers_exclude_store_vendors,
            showcase_hero_image_asset_id = a.showcase_hero_image_asset_id,
            showcase_carousel_image_asset_ids = a.showcase_carousel_image_asset_ids
        FROM admin_ui_settings a
        WHERE a.id = 1
        """
    )
    op.alter_column("parser_pricing_settings", "designers_min_products", server_default=None)
    op.alter_column("parser_pricing_settings", "designers_exclude_store_vendors", server_default=None)
    op.alter_column("parser_pricing_settings", "showcase_carousel_image_asset_ids", server_default=None)
    op.drop_index("idx_admin_ui_settings_updated_at", table_name="admin_ui_settings")
    op.drop_table("admin_ui_settings")
