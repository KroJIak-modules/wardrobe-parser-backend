"""add parser_product external and canonical key fields

Revision ID: 0025_product_external_and_canonical_keys
Revises: 0024_sync_job_runtime
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0025_product_external_and_canonical_keys"
down_revision = "0024_sync_job_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("parser_product", sa.Column("source_external_id", sa.String(length=255), nullable=True))
    op.add_column("parser_product", sa.Column("canonical_url", sa.String(length=2048), nullable=True))
    op.create_index(
        "idx_parser_product_source_external_id",
        "parser_product",
        ["source_id", "source_external_id"],
        unique=False,
    )
    op.create_index(
        "idx_parser_product_source_canonical_url",
        "parser_product",
        ["source_id", "canonical_url"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_parser_product_source_canonical_url", table_name="parser_product")
    op.drop_index("idx_parser_product_source_external_id", table_name="parser_product")
    op.drop_column("parser_product", "canonical_url")
    op.drop_column("parser_product", "source_external_id")
