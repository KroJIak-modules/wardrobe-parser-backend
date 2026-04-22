"""Add parser brand mapping table

Revision ID: 0018_brand_mapping_table
Revises: 0017_prod_overrides_lock
Create Date: 2026-04-22 18:30:00.000000
"""

from alembic import op


revision = "0018_brand_mapping_table"
down_revision = "0017_prod_overrides_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS parser_brand_mapping (
            id SERIAL PRIMARY KEY,
            source_brand VARCHAR(255) NOT NULL,
            source_brand_key VARCHAR(255) NOT NULL UNIQUE,
            target_brand VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_parser_brand_mapping_source_brand
        ON parser_brand_mapping (source_brand)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_parser_brand_mapping_source_brand")
    op.execute("DROP TABLE IF EXISTS parser_brand_mapping")
