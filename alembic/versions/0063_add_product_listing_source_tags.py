"""add source tags to product listings

Revision ID: 0063_add_product_listing_source_tags
Revises: 0062_add_designer_lifecycle_fields
Create Date: 2026-06-21 17:20:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0063_add_product_listing_source_tags"
down_revision = "0062_add_designer_lifecycle_fields"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "product_listings"):
        return
    if not _has_column(bind, "product_listings", "source_tags"):
        op.add_column(
            "product_listings",
            sa.Column(
                "source_tags",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )
    bind.execute(sa.text("update product_listings set source_tags = '[]'::jsonb where source_tags is null"))


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "product_listings") and _has_column(bind, "product_listings", "source_tags"):
        op.drop_column("product_listings", "source_tags")
