"""preserve manual product visibility independently from catalog rules

Revision ID: 0107_add_product_manual_visibility
Revises: 0106_add_sync_job_last_source_name
Create Date: 2026-08-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0107_add_product_manual_visibility"
down_revision = "0106_add_sync_job_last_source_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("is_manually_hidden", sa.Boolean(), nullable=True))
    op.execute("UPDATE products SET is_manually_hidden = (visibility_status = 'hidden') WHERE is_manually_hidden IS NULL")
    op.alter_column("products", "is_manually_hidden", nullable=False, server_default=sa.text("false"))


def downgrade() -> None:
    op.drop_column("products", "is_manually_hidden")
