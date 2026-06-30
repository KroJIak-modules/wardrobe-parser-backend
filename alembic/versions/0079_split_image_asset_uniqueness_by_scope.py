"""split image asset uniqueness by scope

Revision ID: 0079_split_image_asset_uniqueness_by_scope
Revises: 0078_expand_showcase_media_for_viewports
Create Date: 2026-06-29 00:00:01.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "0079_split_image_asset_uniqueness_by_scope"
down_revision = "0078_expand_showcase_media_for_viewports"
branch_labels = None
depends_on = None


def _column_exists(bind: Connection, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _unique_constraint_exists(bind: Connection, table_name: str, constraint_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(constraint.get("name") == constraint_name for constraint in inspector.get_unique_constraints(table_name))


def upgrade() -> None:
    bind = op.get_bind()

    if not _column_exists(bind, "image_assets", "scope"):
        op.add_column("image_assets", sa.Column("scope", sa.String(length=255), nullable=True))

    bind.execute(
        sa.text(
            """
            UPDATE image_assets
            SET scope = CASE
                WHEN storage_key IS NULL OR btrim(storage_key) = '' THEN 'assets'
                WHEN position('/' in storage_key) = 0 THEN 'assets'
                ELSE NULLIF(btrim(split_part(storage_key, '/', 1)), '')
            END
            WHERE scope IS NULL OR btrim(scope) = ''
            """
        )
    )
    bind.execute(sa.text("UPDATE image_assets SET scope = 'assets' WHERE scope IS NULL OR btrim(scope) = ''"))
    op.alter_column("image_assets", "scope", existing_type=sa.String(length=255), nullable=False, server_default="assets")

    if _unique_constraint_exists(bind, "image_assets", "uq_image_assets_checksum_sha256"):
        op.drop_constraint("uq_image_assets_checksum_sha256", "image_assets", type_="unique")
    if not _unique_constraint_exists(bind, "image_assets", "uq_image_assets_scope_checksum_sha256"):
        op.create_unique_constraint(
            "uq_image_assets_scope_checksum_sha256",
            "image_assets",
            ["scope", "checksum_sha256"],
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _unique_constraint_exists(bind, "image_assets", "uq_image_assets_scope_checksum_sha256"):
        op.drop_constraint("uq_image_assets_scope_checksum_sha256", "image_assets", type_="unique")
    if not _unique_constraint_exists(bind, "image_assets", "uq_image_assets_checksum_sha256"):
        op.create_unique_constraint("uq_image_assets_checksum_sha256", "image_assets", ["checksum_sha256"])
    if _column_exists(bind, "image_assets", "scope"):
        op.drop_column("image_assets", "scope")
