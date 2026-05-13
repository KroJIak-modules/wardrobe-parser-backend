"""add sync_job_runtime table for persisted backend sync state

Revision ID: 0024_sync_job_runtime
Revises: 0023_sync_applied_batches
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_sync_job_runtime"
down_revision = "0023_sync_applied_batches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_job_runtime",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("aggregate_job_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("service_job_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expected_db_upserts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("db_upserts_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_products", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_source_name", sa.String(length=255), nullable=True),
        sa.Column("current_source_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_stage", sa.String(length=255), nullable=True),
        sa.Column("products_success", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_error", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("can_cancel", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("sync_job_runtime")
