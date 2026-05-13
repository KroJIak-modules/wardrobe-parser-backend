"""add source attribute visibility flags

Revision ID: 0021_source_attribute_visibility_flags
Revises: 0020_sync_job_runtime
Create Date: 2026-05-13
"""

from alembic import op


revision = "0021_source_attribute_visibility_flags"
down_revision = "0020_sync_job_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE parser_source ADD COLUMN IF NOT EXISTS show_description BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute("ALTER TABLE parser_source ADD COLUMN IF NOT EXISTS show_images BOOLEAN NOT NULL DEFAULT TRUE")


def downgrade() -> None:
    op.execute("ALTER TABLE parser_source DROP COLUMN IF EXISTS show_images")
    op.execute("ALTER TABLE parser_source DROP COLUMN IF EXISTS show_description")

