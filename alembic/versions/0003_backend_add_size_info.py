"""add size and additional_info to products

Revision ID: 0003_backend_add_size_info
Revises: 0001_backend_init
Create Date: 2026-02-11 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_backend_add_size_info"
down_revision = "0002_parser_core_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("size", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("additional_info", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "additional_info")
    op.drop_column("products", "size")
