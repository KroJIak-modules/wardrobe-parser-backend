"""add jpy_to_usd_rate to parser_pricing_settings

Revision ID: 0021_add_jpy_to_usd_rate
Revises: 0020_admin_ui_settings_split
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_add_jpy_to_usd_rate"
down_revision = "0020_admin_ui_settings_split"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parser_pricing_settings",
        sa.Column("jpy_to_usd_rate", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("parser_pricing_settings", "jpy_to_usd_rate")
