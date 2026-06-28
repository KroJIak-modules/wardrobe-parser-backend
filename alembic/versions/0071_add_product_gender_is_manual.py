"""add product gender_is_manual flag

Revision ID: 0071_add_product_gender_is_manual
Revises: 0070_rewrite_dedup_product_state
Create Date: 2026-06-25 11:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0071_add_product_gender_is_manual"
down_revision = "0070_rewrite_dedup_product_state"
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "products", "gender_is_manual"):
        op.add_column(
            "products",
            sa.Column("gender_is_manual", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "products", "gender_is_manual"):
        op.drop_column("products", "gender_is_manual")
