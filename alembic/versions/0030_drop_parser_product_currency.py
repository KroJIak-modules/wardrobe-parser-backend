"""Drop parser_product.currency column (currency is variant-level only).

Revision ID: 0030_drop_parser_product_currency
Revises: 0029_product_origin_variant
Create Date: 2026-05-20 21:35:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0030_drop_parser_product_currency"
down_revision = "0029_product_origin_variant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("parser_product")}
    if "currency" in columns:
        op.drop_column("parser_product", "currency")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("parser_product")}
    if "currency" not in columns:
        op.add_column(
            "parser_product",
            sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        )
