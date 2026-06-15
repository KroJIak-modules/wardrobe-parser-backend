"""drop parser_product.price

Revision ID: 0039_drop_parser_product_price
Revises: 0038_parser_product_gender
Create Date: 2026-06-13 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0039_drop_parser_product_price"
down_revision = "0038_parser_product_gender"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("parser_product") as batch_op:
        batch_op.drop_column("price")


def downgrade() -> None:
    with op.batch_alter_table("parser_product") as batch_op:
        batch_op.add_column(sa.Column("price", sa.Float(), nullable=True))
