"""drop raw_data from products

Revision ID: 0006_backend_drop_raw_data
Revises: 0005_backend_add_size_data
Create Date: 2026-02-11 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_backend_drop_raw_data"
down_revision = "0005_backend_add_size_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("products", "raw_data")


def downgrade() -> None:
    op.add_column("products", sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
