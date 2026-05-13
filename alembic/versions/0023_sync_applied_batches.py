"""add sync_applied_batch table for persistent batch idempotency

Revision ID: 0023_sync_applied_batches
Revises: 0022_jpy_to_usd_nullable_no_default
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_sync_applied_batches"
down_revision = "0022_jpy_to_usd_nullable_no_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_applied_batch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("aggregate_job_id", sa.String(length=64), nullable=False),
        sa.Column("service_job_id", sa.String(length=64), nullable=False),
        sa.Column("batch_id", sa.String(length=255), nullable=False),
        sa.Column("source_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("service_job_id", "batch_id", name="uq_sync_applied_batch_service_job_batch"),
    )


def downgrade() -> None:
    op.drop_table("sync_applied_batch")
