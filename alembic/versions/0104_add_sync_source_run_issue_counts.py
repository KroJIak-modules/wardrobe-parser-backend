"""store aggregated product issues for each sync source run

Revision ID: 0104_add_sync_source_run_issue_counts
Revises: 0103_enable_public_title_cleaning_by_default
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0104_add_sync_source_run_issue_counts"
down_revision = "0103_enable_public_title_cleaning_by_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sync_job_source_runs",
        sa.Column("issue_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("sync_job_source_runs", "issue_counts")
