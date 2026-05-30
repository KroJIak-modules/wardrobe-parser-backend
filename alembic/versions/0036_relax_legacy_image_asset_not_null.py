"""relax legacy image_asset not-null constraints

Revision ID: 0036_relax_legacy_image_asset_not_null
Revises: 0035_image_asset_schema_alignment
Create Date: 2026-05-30 15:40:00
"""

from alembic import op
from sqlalchemy import text


revision = "0036_relax_legacy_image_asset_not_null"
down_revision = "0035_image_asset_schema_alignment"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = :table
              AND column_name = :column
            LIMIT 1
            """
        ),
        {"table": table, "column": column},
    ).first()
    return row is not None


def upgrade() -> None:
    legacy_columns = ["kind", "storage_rel_path", "mime_type", "size_bytes", "sha256"]
    for column in legacy_columns:
        if _column_exists("image_asset", column):
            op.execute(f"ALTER TABLE image_asset ALTER COLUMN {column} DROP NOT NULL")


def downgrade() -> None:
    # No-op by design: re-applying NOT NULL can break existing runtime rows.
    pass

