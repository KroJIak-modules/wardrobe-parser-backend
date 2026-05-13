"""add auto sync period minutes to admin ui settings

Revision ID: 0028_admin_ui_auto_sync_period_minutes
Revises: 0027_source_sync_telemetry_fields
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa

revision = "0028_admin_ui_auto_sync_period_minutes"
down_revision = "0027_source_sync_telemetry_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("admin_ui_settings", sa.Column("auto_sync_period_minutes", sa.Integer(), nullable=True))
    op.execute("UPDATE admin_ui_settings SET auto_sync_period_minutes = 60 WHERE auto_sync_period_minutes IS NULL")
    op.alter_column("admin_ui_settings", "auto_sync_period_minutes", nullable=False, server_default="60")


def downgrade() -> None:
    op.drop_column("admin_ui_settings", "auto_sync_period_minutes")
