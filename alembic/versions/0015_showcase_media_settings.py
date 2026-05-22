"""add showcase media settings

Revision ID: 0015_showcase_media_settings
Revises: 0014_seed_canonical_ssr
Create Date: 2026-04-16 23:10:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015_showcase_media_settings"
down_revision = "0014_seed_canonical_ssr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {str(c.get("name") or "").strip() for c in inspector.get_columns("parser_pricing_settings")}

    if "showcase_hero_image_asset_id" not in columns:
        op.add_column(
            "parser_pricing_settings",
            sa.Column("showcase_hero_image_asset_id", sa.Integer(), nullable=True),
        )
    if "showcase_carousel_image_asset_ids" not in columns:
        op.add_column(
            "parser_pricing_settings",
            sa.Column("showcase_carousel_image_asset_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        )
        op.alter_column("parser_pricing_settings", "showcase_carousel_image_asset_ids", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {str(c.get("name") or "").strip() for c in inspector.get_columns("parser_pricing_settings")}
    if "showcase_carousel_image_asset_ids" in columns:
        op.drop_column("parser_pricing_settings", "showcase_carousel_image_asset_ids")
    if "showcase_hero_image_asset_id" in columns:
        op.drop_column("parser_pricing_settings", "showcase_hero_image_asset_id")
