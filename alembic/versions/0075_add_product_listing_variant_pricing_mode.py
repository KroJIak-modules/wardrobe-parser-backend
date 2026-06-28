"""add product listing variant pricing mode

Revision ID: 0075_add_product_listing_variant_pricing_mode
Revises: 0074_drop_product_price_overrides
Create Date: 2026-06-27 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "0075_add_product_listing_variant_pricing_mode"
down_revision = "0074_drop_product_price_overrides"
branch_labels = None
depends_on = None


def _column_exists(bind: Connection, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "product_listing_variants", "pricing_mode"):
        op.add_column(
            "product_listing_variants",
            sa.Column("pricing_mode", sa.String(length=32), nullable=False, server_default="source"),
        )
    op.execute(
        """
        UPDATE product_listing_variants
        SET pricing_mode = 'source'
        WHERE pricing_mode IS NULL OR btrim(pricing_mode) = ''
        """
    )
    op.create_check_constraint(
        "ck_product_listing_variants_pricing_mode",
        "product_listing_variants",
        "pricing_mode IN ('source', 'fixed_final_rub')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    constraints = {constraint.get("name") for constraint in inspector.get_check_constraints("product_listing_variants")}
    if "ck_product_listing_variants_pricing_mode" in constraints:
        op.drop_constraint("ck_product_listing_variants_pricing_mode", "product_listing_variants", type_="check")
    if _column_exists(bind, "product_listing_variants", "pricing_mode"):
        op.drop_column("product_listing_variants", "pricing_mode")
