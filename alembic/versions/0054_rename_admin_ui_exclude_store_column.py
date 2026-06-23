"""rename admin ui exclude-store column

Revision ID: 0054_rename_admin_ui_exclude_store_column
Revises: 0053_designer_case_insensitive_uniques
Create Date: 2026-06-20 07:15:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0054_rename_admin_ui_exclude_store_column"
down_revision = "0053_designer_case_insensitive_uniques"
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "admin_ui_settings", "designers_exclude_store_vendors") and not _has_column(bind, "admin_ui_settings", "designers_exclude_store_names"):
        op.alter_column("admin_ui_settings", "designers_exclude_store_vendors", new_column_name="designers_exclude_store_names")


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "admin_ui_settings", "designers_exclude_store_names") and not _has_column(bind, "admin_ui_settings", "designers_exclude_store_vendors"):
        op.alter_column("admin_ui_settings", "designers_exclude_store_names", new_column_name="designers_exclude_store_vendors")
