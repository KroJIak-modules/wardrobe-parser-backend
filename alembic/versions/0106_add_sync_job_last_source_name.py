"""store the most recently active sync source name

Revision ID: 0106_add_sync_job_last_source_name
Revises: 0105_add_sync_job_current_stage
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0106_add_sync_job_last_source_name"
down_revision = "0105_add_sync_job_current_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_jobs", sa.Column("last_source_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("sync_jobs", "last_source_name")
