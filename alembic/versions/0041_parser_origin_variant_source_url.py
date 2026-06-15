"""rename parser_product_origin_variant.source_product_url to source_url

Revision ID: 0041_parser_origin_variant_source_url
Revises: 0040_parser_product_contract_cleanup
Create Date: 2026-06-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0041_parser_origin_variant_source_url"
down_revision = "0040_parser_product_contract_cleanup"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if _column_exists("parser_product_origin_variant", "source_product_url") and not _column_exists("parser_product_origin_variant", "source_url"):
        with op.batch_alter_table("parser_product_origin_variant") as batch_op:
            batch_op.alter_column("source_product_url", new_column_name="source_url", existing_type=sa.String(length=2048))


def downgrade() -> None:
    if _column_exists("parser_product_origin_variant", "source_url") and not _column_exists("parser_product_origin_variant", "source_product_url"):
        with op.batch_alter_table("parser_product_origin_variant") as batch_op:
            batch_op.alter_column("source_url", new_column_name="source_product_url", existing_type=sa.String(length=2048))
