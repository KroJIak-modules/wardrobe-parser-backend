"""add parser_product.gender and reset legacy parser products

Revision ID: 0038_parser_product_gender
Revises: 0037_drop_legacy_shipping_rules
Create Date: 2026-06-13 19:30:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = "0038_parser_product_gender"
down_revision = "0037_drop_legacy_shipping_rules"
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
    if not _column_exists("parser_product", "gender"):
        op.add_column(
            "parser_product",
            sa.Column("gender", sa.String(length=16), nullable=False, server_default="unisex"),
        )

    op.execute(text("DELETE FROM parser_dedup_decision"))
    op.execute(text("DELETE FROM parser_favorite_product"))
    op.execute(text("DELETE FROM parser_product_category_match"))
    op.execute(text("DELETE FROM parser_category_manual_product"))
    op.execute(text("DELETE FROM parser_product_origin_variant"))
    op.execute(text("DELETE FROM parser_product"))


def downgrade() -> None:
    op.execute(text("DELETE FROM parser_dedup_decision"))
    op.execute(text("DELETE FROM parser_favorite_product"))
    op.execute(text("DELETE FROM parser_product_category_match"))
    op.execute(text("DELETE FROM parser_category_manual_product"))
    op.execute(text("DELETE FROM parser_product_origin_variant"))
    op.execute(text("DELETE FROM parser_product"))
    if _column_exists("parser_product", "gender"):
        op.drop_column("parser_product", "gender")
