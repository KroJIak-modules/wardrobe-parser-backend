"""expand showcase media for viewports

Revision ID: 0078_expand_showcase_media_for_viewports
Revises: 0077_add_source_sort_priority
Create Date: 2026-06-29 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "0078_expand_showcase_media_for_viewports"
down_revision = "0077_add_source_sort_priority"
branch_labels = None
depends_on = None


def _column_exists(bind: Connection, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _constraint_exists(bind: Connection, table_name: str, constraint_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(constraint.get("name") == constraint_name for constraint in inspector.get_unique_constraints(table_name)) or any(
        constraint.get("name") == constraint_name for constraint in inspector.get_check_constraints(table_name)
    )


def upgrade() -> None:
    bind = op.get_bind()

    if not _column_exists(bind, "showcase_settings", "desktop_hero_image_asset_id"):
        op.add_column(
            "showcase_settings",
            sa.Column("desktop_hero_image_asset_id", sa.BigInteger(), nullable=True),
        )
        op.create_foreign_key(
            "fk_showcase_settings_desktop_hero_image_asset_id",
            "showcase_settings",
            "image_assets",
            ["desktop_hero_image_asset_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if not _column_exists(bind, "showcase_settings", "mobile_hero_image_asset_id"):
        op.add_column(
            "showcase_settings",
            sa.Column("mobile_hero_image_asset_id", sa.BigInteger(), nullable=True),
        )
        op.create_foreign_key(
            "fk_showcase_settings_mobile_hero_image_asset_id",
            "showcase_settings",
            "image_assets",
            ["mobile_hero_image_asset_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if _column_exists(bind, "showcase_settings", "hero_image_asset_id"):
        bind.execute(
            sa.text(
                """
                UPDATE showcase_settings
                SET desktop_hero_image_asset_id = hero_image_asset_id
                WHERE hero_image_asset_id IS NOT NULL
                  AND desktop_hero_image_asset_id IS NULL
                """
            )
        )
        op.drop_column("showcase_settings", "hero_image_asset_id")

    if not _column_exists(bind, "showcase_carousel_images", "viewport"):
        op.add_column("showcase_carousel_images", sa.Column("viewport", sa.String(length=16), nullable=True))
    bind.execute(
        sa.text(
            """
            UPDATE showcase_carousel_images
            SET viewport = 'desktop'
            WHERE viewport IS NULL OR btrim(viewport) = ''
            """
        )
    )
    op.alter_column("showcase_carousel_images", "viewport", existing_type=sa.String(length=16), nullable=False)

    if _constraint_exists(bind, "showcase_carousel_images", "uq_showcase_carousel_images_position"):
        op.drop_constraint("uq_showcase_carousel_images_position", "showcase_carousel_images", type_="unique")
    if not _constraint_exists(bind, "showcase_carousel_images", "uq_showcase_carousel_images_viewport_position"):
        op.create_unique_constraint(
            "uq_showcase_carousel_images_viewport_position",
            "showcase_carousel_images",
            ["viewport", "position"],
        )
    if not _constraint_exists(bind, "showcase_carousel_images", "ck_showcase_carousel_images_viewport"):
        op.create_check_constraint(
            "ck_showcase_carousel_images_viewport",
            "showcase_carousel_images",
            "viewport IN ('desktop', 'mobile')",
        )


def downgrade() -> None:
    bind = op.get_bind()

    if not _column_exists(bind, "showcase_settings", "hero_image_asset_id"):
        op.add_column("showcase_settings", sa.Column("hero_image_asset_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            "fk_showcase_settings_hero_image_asset_id",
            "showcase_settings",
            "image_assets",
            ["hero_image_asset_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if _column_exists(bind, "showcase_settings", "desktop_hero_image_asset_id"):
        bind.execute(
            sa.text(
                """
                UPDATE showcase_settings
                SET hero_image_asset_id = desktop_hero_image_asset_id
                WHERE desktop_hero_image_asset_id IS NOT NULL
                """
            )
        )
        op.drop_constraint("fk_showcase_settings_desktop_hero_image_asset_id", "showcase_settings", type_="foreignkey")
        op.drop_column("showcase_settings", "desktop_hero_image_asset_id")
    if _column_exists(bind, "showcase_settings", "mobile_hero_image_asset_id"):
        op.drop_constraint("fk_showcase_settings_mobile_hero_image_asset_id", "showcase_settings", type_="foreignkey")
        op.drop_column("showcase_settings", "mobile_hero_image_asset_id")

    if _constraint_exists(bind, "showcase_carousel_images", "ck_showcase_carousel_images_viewport"):
        op.drop_constraint("ck_showcase_carousel_images_viewport", "showcase_carousel_images", type_="check")
    if _constraint_exists(bind, "showcase_carousel_images", "uq_showcase_carousel_images_viewport_position"):
        op.drop_constraint("uq_showcase_carousel_images_viewport_position", "showcase_carousel_images", type_="unique")
    if not _constraint_exists(bind, "showcase_carousel_images", "uq_showcase_carousel_images_position"):
        op.create_unique_constraint("uq_showcase_carousel_images_position", "showcase_carousel_images", ["position"])
    if _column_exists(bind, "showcase_carousel_images", "viewport"):
        op.drop_column("showcase_carousel_images", "viewport")
