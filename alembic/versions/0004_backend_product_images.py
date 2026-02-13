"""add product_images table

Revision ID: 0004_backend_product_images
Revises: 0003_backend_add_size_info
Create Date: 2026-02-11 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_backend_product_images"
down_revision = "0003_backend_add_size_info"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_images",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("product_id", "url", name="uq_product_images_product_url"),
    )
    op.create_index("ix_product_images_id", "product_images", ["id"], unique=False)
    op.create_index("ix_product_images_product_id", "product_images", ["product_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_product_images_product_id", table_name="product_images")
    op.drop_index("ix_product_images_id", table_name="product_images")
    op.drop_table("product_images")
