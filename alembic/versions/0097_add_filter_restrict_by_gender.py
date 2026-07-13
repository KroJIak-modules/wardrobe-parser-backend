"""add filter restrict_by_gender

Revision ID: 0097_add_filter_restrict_by_gender
Revises: 0096_add_site_access_settings
Create Date: 2026-07-13 17:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0097_add_filter_restrict_by_gender"
down_revision = "0096_add_site_access_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "filters",
        sa.Column("restrict_by_gender", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("filters", "restrict_by_gender")
