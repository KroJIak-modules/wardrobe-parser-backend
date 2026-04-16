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
    op.add_column(
        "parser_pricing_settings",
        sa.Column("showcase_hero_image_asset_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "parser_pricing_settings",
        sa.Column("showcase_carousel_image_asset_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.alter_column("parser_pricing_settings", "showcase_carousel_image_asset_ids", server_default=None)


def downgrade() -> None:
    op.drop_column("parser_pricing_settings", "showcase_carousel_image_asset_ids")
    op.drop_column("parser_pricing_settings", "showcase_hero_image_asset_id")
