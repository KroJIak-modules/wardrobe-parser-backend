"""make jpy_to_usd_rate nullable without default

Revision ID: 0022_jpy_to_usd_nullable_no_default
Revises: 0021_add_jpy_to_usd_rate
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa

revision = "0022_jpy_to_usd_nullable_no_default"
down_revision = "0021_add_jpy_to_usd_rate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("parser_pricing_settings", "jpy_to_usd_rate", server_default=None, existing_type=sa.Float(), existing_nullable=False)
    op.alter_column("parser_pricing_settings", "jpy_to_usd_rate", nullable=True, existing_type=sa.Float())


def downgrade() -> None:
    op.alter_column("parser_pricing_settings", "jpy_to_usd_rate", nullable=False, existing_type=sa.Float())
    op.alter_column("parser_pricing_settings", "jpy_to_usd_rate", server_default="0.0062", existing_type=sa.Float())
