"""add parser_product_origin_variant and backfill from parser_product

Revision ID: 0029_product_origin_variant
Revises: 0028_admin_ui_auto_sync_period_minutes
Create Date: 2026-05-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0029_product_origin_variant"
down_revision = "0028_admin_ui_auto_sync_period_minutes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parser_product_origin_variant",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("origin_key", sa.String(length=512), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("source_product_url", sa.String(length=2048), nullable=False),
        sa.Column("source_variant_id", sa.String(length=255), nullable=True),
        sa.Column("source_variant_title", sa.String(length=1024), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["product_id"], ["parser_product.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["parser_source.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("origin_key", name="uq_parser_product_origin_variant_origin_key"),
    )
    op.create_index("idx_parser_origin_variant_product_id", "parser_product_origin_variant", ["product_id"])
    op.create_index("idx_parser_origin_variant_source_id", "parser_product_origin_variant", ["source_id"])

    # Backfill one synthetic variant-link per existing product to preserve source filtering semantics.
    op.execute(
        """
        INSERT INTO parser_product_origin_variant
        (origin_key, product_id, source_id, source_product_url, source_variant_id, source_variant_title, sku, price, currency, available, payload)
        SELECT
          'legacy:' || p.id::text,
          p.id,
          p.source_id,
          COALESCE(NULLIF(TRIM(p.url), ''), 'legacy://product/' || p.id::text),
          NULL,
          NULL,
          NULL,
          p.price,
          p.currency,
          CASE WHEN p.status = 'available' THEN true ELSE false END,
          '{}'::json
        FROM parser_product p
        WHERE p.deleted_at IS NULL
        ON CONFLICT (origin_key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("idx_parser_origin_variant_source_id", table_name="parser_product_origin_variant")
    op.drop_index("idx_parser_origin_variant_product_id", table_name="parser_product_origin_variant")
    op.drop_table("parser_product_origin_variant")
