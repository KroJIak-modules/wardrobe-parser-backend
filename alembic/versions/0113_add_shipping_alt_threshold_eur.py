"""add shipping alt threshold to pricing settings

Revision ID: 0113_add_shipping_alt_threshold_eur
Revises: 0112_add_admin_designer_views
Create Date: 2026-10-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0113_add_shipping_alt_threshold_eur"
down_revision = "0112_add_admin_designer_views"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pricing_settings",
        sa.Column(
            "shipping_alt_threshold_eur",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("300"),
        ),
    )
    op.alter_column("pricing_settings", "shipping_alt_threshold_eur", server_default=None)


def downgrade() -> None:
    op.drop_column("pricing_settings", "shipping_alt_threshold_eur")
