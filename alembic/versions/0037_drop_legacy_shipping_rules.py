"""drop legacy pricing shipping_rules column

Revision ID: 0037_drop_legacy_shipping_rules
Revises: 0036_relax_legacy_image_asset_not_null
Create Date: 2026-06-07 13:35:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = "0037_drop_legacy_shipping_rules"
down_revision = "0036_relax_legacy_image_asset_not_null"
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
    if _column_exists("parser_pricing_settings", "shipping_rules"):
        op.drop_column("parser_pricing_settings", "shipping_rules")


def downgrade() -> None:
    if not _column_exists("parser_pricing_settings", "shipping_rules"):
        op.add_column(
            "parser_pricing_settings",
            sa.Column("shipping_rules", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        )
