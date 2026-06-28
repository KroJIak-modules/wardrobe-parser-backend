"""add product source_gender

Revision ID: 0072_add_product_source_gender
Revises: 0071_add_product_gender_is_manual
Create Date: 2026-06-25 13:20:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0072_add_product_source_gender"
down_revision = "0071_add_product_gender_is_manual"
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "products", "source_gender"):
        op.add_column(
            "products",
            sa.Column("source_gender", sa.String(length=16), nullable=True),
        )
        op.execute("UPDATE products SET source_gender = gender WHERE source_gender IS NULL")
        op.alter_column("products", "source_gender", nullable=False, server_default=sa.text("'unisex'"))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "products", "source_gender"):
        op.drop_column("products", "source_gender")
