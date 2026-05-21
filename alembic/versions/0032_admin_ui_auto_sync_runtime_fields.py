"""Add admin UI auto-sync runtime state fields.

Revision ID: 0032_admin_ui_auto_sync_runtime_fields
Revises: 0031_admin_rbac_users_roles
Create Date: 2026-05-21 16:10:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0032_admin_ui_auto_sync_runtime_fields"
down_revision = "0031_admin_rbac_users_roles"
branch_labels = None
depends_on = None


def _column_names(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    try:
        columns = inspector.get_columns(table_name)
    except Exception:
        return set()
    return {str(col.get("name") or "").strip() for col in columns}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind, "admin_ui_settings")

    if "auto_sync_next_run_at" not in columns:
        op.add_column("admin_ui_settings", sa.Column("auto_sync_next_run_at", sa.DateTime(timezone=True), nullable=True))
    if "auto_sync_last_started_at" not in columns:
        op.add_column("admin_ui_settings", sa.Column("auto_sync_last_started_at", sa.DateTime(timezone=True), nullable=True))
    if "auto_sync_last_finished_at" not in columns:
        op.add_column("admin_ui_settings", sa.Column("auto_sync_last_finished_at", sa.DateTime(timezone=True), nullable=True))
    if "auto_sync_last_status" not in columns:
        op.add_column("admin_ui_settings", sa.Column("auto_sync_last_status", sa.String(length=32), nullable=True))
    if "auto_sync_last_error" not in columns:
        op.add_column("admin_ui_settings", sa.Column("auto_sync_last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind, "admin_ui_settings")

    if "auto_sync_last_error" in columns:
        op.drop_column("admin_ui_settings", "auto_sync_last_error")
    if "auto_sync_last_status" in columns:
        op.drop_column("admin_ui_settings", "auto_sync_last_status")
    if "auto_sync_last_finished_at" in columns:
        op.drop_column("admin_ui_settings", "auto_sync_last_finished_at")
    if "auto_sync_last_started_at" in columns:
        op.drop_column("admin_ui_settings", "auto_sync_last_started_at")
    if "auto_sync_next_run_at" in columns:
        op.drop_column("admin_ui_settings", "auto_sync_next_run_at")
