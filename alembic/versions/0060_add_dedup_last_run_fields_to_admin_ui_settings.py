"""add dedup last run fields to admin ui settings

Revision ID: 0060_add_dedup_last_run_fields_to_admin_ui_settings
Revises: 0059_add_dedup_candidates_and_source_dedup_flag
Create Date: 2026-06-21 13:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy import inspect


revision = "0060_add_dedup_last_run_fields_to_admin_ui_settings"
down_revision = "0059_add_dedup_candidates_and_source_dedup_flag"
branch_labels = None
depends_on = None


def _column_names(bind: Connection, table_name: str) -> set[str]:
    inspector = inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind, "admin_ui_settings")
    if "dedup_last_started_at" not in columns:
        op.add_column("admin_ui_settings", sa.Column("dedup_last_started_at", sa.DateTime(timezone=True), nullable=True))
    if "dedup_last_finished_at" not in columns:
        op.add_column("admin_ui_settings", sa.Column("dedup_last_finished_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind, "admin_ui_settings")
    if "dedup_last_finished_at" in columns:
        op.drop_column("admin_ui_settings", "dedup_last_finished_at")
    if "dedup_last_started_at" in columns:
        op.drop_column("admin_ui_settings", "dedup_last_started_at")
