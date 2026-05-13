"""Add include_in_designers flag to parser_brand_mapping

Revision ID: 0019_brand_mapping_include_in_designers
Revises: 0018_brand_mapping_table
Create Date: 2026-04-22 19:00:00.000000
"""

from alembic import op


revision = "0019_brand_mapping_include_in_designers"
down_revision = "0018_brand_mapping_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE parser_brand_mapping ADD COLUMN IF NOT EXISTS include_in_designers BOOLEAN NOT NULL DEFAULT TRUE")


def downgrade() -> None:
    op.execute("ALTER TABLE parser_brand_mapping DROP COLUMN IF EXISTS include_in_designers")
