"""add site sort price range flag to products

Revision ID: 0119_add_site_sort_price_has_range
Revises: 0118_drop_obsolete_account_permissions
Create Date: 2026-11-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0119_add_site_sort_price_has_range"
down_revision = "0118_drop_obsolete_account_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "site_sort_price_has_range",
            sa.Boolean(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("products", "site_sort_price_has_range")
