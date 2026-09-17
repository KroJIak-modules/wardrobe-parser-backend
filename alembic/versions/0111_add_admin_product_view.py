"""track which products each admin has viewed

Revision ID: 0111_add_admin_product_view
Revises: 0110_index_gallery_images_by_listing_image
Create Date: 2026-09-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0111_add_admin_product_view"
down_revision = "0110_index_gallery_images_by_listing_image"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_product_view",
        sa.Column("admin_user_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("admin_user_id", "product_id"),
    )
    op.create_index(
        "ix_admin_product_view_product_user",
        "admin_product_view",
        ["product_id", "admin_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_admin_product_view_product_user", table_name="admin_product_view")
    op.drop_table("admin_product_view")
