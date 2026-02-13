"""add size_data to products

Revision ID: 0005_backend_add_size_data
Revises: 0004_backend_product_images
Create Date: 2026-02-11 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_backend_add_size_data"
down_revision = "0004_backend_product_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("size_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "size_data")
