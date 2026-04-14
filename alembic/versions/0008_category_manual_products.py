"""add manual category product mapping

Revision ID: 0008_category_manual_products
Revises: 0007_category_kw_scope
Create Date: 2026-04-14 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_category_manual_products"
down_revision = "0007_category_kw_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parser_category_manual_product",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["parser_category.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["parser_product.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category_id", "product_id", name="uq_parser_category_manual_product"),
    )
    op.create_index("idx_parser_category_manual_category", "parser_category_manual_product", ["category_id"], unique=False)
    op.create_index("idx_parser_category_manual_product", "parser_category_manual_product", ["product_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_parser_category_manual_product", table_name="parser_category_manual_product")
    op.drop_index("idx_parser_category_manual_category", table_name="parser_category_manual_product")
    op.drop_table("parser_category_manual_product")
