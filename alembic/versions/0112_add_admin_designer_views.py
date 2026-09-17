"""per-admin viewed marks for designer editor brands and designers

Revision ID: 0112_add_admin_designer_views
Revises: 0111_add_admin_product_view
Create Date: 2026-09-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0112_add_admin_designer_views"
down_revision = "0111_add_admin_product_view"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_brand_view",
        sa.Column("admin_user_id", sa.Integer(), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("admin_user_id", "source_name"),
    )
    op.create_table(
        "admin_designer_view",
        sa.Column("admin_user_id", sa.Integer(), nullable=False),
        sa.Column("designer_id", sa.BigInteger(), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["designer_id"], ["designers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("admin_user_id", "designer_id"),
    )
    op.create_index(
        "ix_admin_designer_view_designer_user",
        "admin_designer_view",
        ["designer_id", "admin_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_admin_designer_view_designer_user", table_name="admin_designer_view")
    op.drop_table("admin_designer_view")
    op.drop_table("admin_brand_view")
