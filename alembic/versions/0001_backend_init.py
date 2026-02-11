"""init backend tables

Revision ID: 0001_backend_init
Revises: 
Create Date: 2026-02-11 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_backend_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sites",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.Column("last_status_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key", name="uq_sites_key"),
    )
    op.create_index("ix_sites_id", "sites", ["id"], unique=False)
    op.create_index("ix_sites_key", "sites", ["key"], unique=True)

    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("site_id", sa.BigInteger(), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("product_url", sa.String(length=1024), nullable=False),
        sa.Column("image_url", sa.String(length=1024), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("parser_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("site_id", "external_id", name="uq_products_site_external"),
    )
    op.create_index("ix_products_id", "products", ["id"], unique=False)
    op.create_index("ix_products_site_id", "products", ["site_id"], unique=False)
    op.create_index("ix_products_external_id", "products", ["external_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_products_external_id", table_name="products")
    op.drop_index("ix_products_site_id", table_name="products")
    op.drop_index("ix_products_id", table_name="products")
    op.drop_table("products")
    op.drop_index("ix_sites_key", table_name="sites")
    op.drop_index("ix_sites_id", table_name="sites")
    op.drop_table("sites")
