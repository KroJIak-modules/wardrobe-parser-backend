"""add designers min products threshold setting

Revision ID: 0011_designers_min_products
Revises: 0010_category_slug_muzhskoe_zhenskoe
Create Date: 2026-04-16 06:45:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0011_designers_min_products"
down_revision = "0010_category_slug_ru_style"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parser_pricing_settings",
        sa.Column("designers_min_products", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute(
        """
        UPDATE parser_pricing_settings
        SET designers_min_products = 1
        WHERE designers_min_products IS NULL OR designers_min_products < 1
        """
    )
    op.alter_column("parser_pricing_settings", "designers_min_products", server_default=None)


def downgrade() -> None:
    op.drop_column("parser_pricing_settings", "designers_min_products")
