"""add designer logo image asset reference

Revision ID: 0056_designer_logo_image_asset_id
Revises: 0055_add_dedup_undo_payload
Create Date: 2026-06-21 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0056_designer_logo_image_asset_id"
down_revision = "0055_add_dedup_undo_payload"
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
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


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "designers", "logo_image_asset_id"):
        op.drop_column("designers", "logo_image_asset_id")
