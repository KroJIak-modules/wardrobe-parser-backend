"""move logo from designers to sources

Revision ID: 0065_move_logo_from_designers_to_sources
Revises: 0064_add_product_filter_assignments
Create Date: 2026-06-23 03:10:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.engine import Connection


revision = "0065_move_logo_from_designers_to_sources"
down_revision = "0064_add_product_filter_assignments"
branch_labels = None
depends_on = None


def _has_column(bind: Connection, table_name: str, column_name: str) -> bool:
    columns = inspect(bind).get_columns(table_name)
    return any(str(column.get("name")) == column_name for column in columns)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "sources", "logo_image_asset_id"):
        op.add_column(
            "sources",
            sa.Column(
                "logo_image_asset_id",
                sa.BigInteger(),
                sa.ForeignKey("image_assets.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )

    if _has_column(bind, "designers", "logo_image_asset_id"):
        op.drop_column("designers", "logo_image_asset_id")


def downgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "designers", "logo_image_asset_id"):
        op.add_column(
            "designers",
            sa.Column(
                "logo_image_asset_id",
                sa.BigInteger(),
                sa.ForeignKey("image_assets.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )

    if _has_column(bind, "sources", "logo_image_asset_id"):
        op.drop_column("sources", "logo_image_asset_id")
