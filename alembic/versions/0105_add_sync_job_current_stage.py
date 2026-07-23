"""store current sync job activity for the admin status card

Revision ID: 0105_add_sync_job_current_stage
Revises: 0104_add_sync_source_run_issue_counts
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0105_add_sync_job_current_stage"
down_revision = "0104_add_sync_source_run_issue_counts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_jobs", sa.Column("current_stage_code", sa.String(length=64), nullable=True))
    op.add_column("sync_jobs", sa.Column("current_stage_label", sa.String(length=255), nullable=True))
    op.add_column("sync_jobs", sa.Column("current_stage_detail", sa.String(length=512), nullable=True))
    op.add_column("sync_jobs", sa.Column("current_stage_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("sync_jobs", "current_stage_updated_at")
    op.drop_column("sync_jobs", "current_stage_detail")
    op.drop_column("sync_jobs", "current_stage_label")
    op.drop_column("sync_jobs", "current_stage_code")
