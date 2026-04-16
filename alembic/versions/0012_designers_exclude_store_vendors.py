"""add toggle to exclude store-like vendors in designers

Revision ID: 0012_designers_excl_store
Revises: 0011_designers_min_products
Create Date: 2026-04-16 07:25:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_designers_excl_store"
down_revision = "0011_designers_min_products"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parser_pricing_settings",
        sa.Column("designers_exclude_store_vendors", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.alter_column("parser_pricing_settings", "designers_exclude_store_vendors", server_default=None)


def downgrade() -> None:
    op.drop_column("parser_pricing_settings", "designers_exclude_store_vendors")
