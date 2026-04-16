"""add category index snapshot tables

Revision ID: 0009_category_index_snapshot
Revises: 0008_category_manual_products
Create Date: 2026-04-16 00:00:02.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_category_index_snapshot"
down_revision = "0008_category_manual_products"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parser_product_category_match",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("match_source", sa.String(length=16), nullable=False, server_default="auto"),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["parser_category.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["parser_product.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "category_id", "match_source", name="uq_parser_product_category_match"),
    )
    op.create_index("idx_parser_product_category_match_product", "parser_product_category_match", ["product_id"], unique=False)
    op.create_index("idx_parser_product_category_match_category", "parser_product_category_match", ["category_id"], unique=False)
    op.create_index("idx_parser_product_category_match_source", "parser_product_category_match", ["match_source"], unique=False)

    op.create_table(
        "parser_category_count_snapshot",
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("direct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subtree_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["parser_category.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("category_id"),
    )

    op.create_table(
        "parser_category_index_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("matches_built_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counts_built_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("parser_category_index_state")
    op.drop_table("parser_category_count_snapshot")
    op.drop_index("idx_parser_product_category_match_source", table_name="parser_product_category_match")
    op.drop_index("idx_parser_product_category_match_category", table_name="parser_product_category_match")
    op.drop_index("idx_parser_product_category_match_product", table_name="parser_product_category_match")
    op.drop_table("parser_product_category_match")
