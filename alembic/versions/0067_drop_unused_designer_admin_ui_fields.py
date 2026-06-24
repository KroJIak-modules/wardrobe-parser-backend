"""drop unused designer admin ui fields

Revision ID: 0067_drop_unused_designer_admin_ui_fields
Revises: 0066_add_bybit_runtime_columns_to_pricing_settings
Create Date: 2026-06-23 12:10:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.engine import Connection


revision = "0067_drop_unused_designer_admin_ui_fields"
down_revision = "0066_add_bybit_runtime_columns_to_pricing_settings"
branch_labels = None
depends_on = None


def _columns(bind: Connection, table_name: str) -> set[str]:
    return {str(column.get("name") or "").strip() for column in inspect(bind).get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "admin_ui_settings")

    if "designers_exclude_store_names" in columns:
        op.drop_column("admin_ui_settings", "designers_exclude_store_names")
    if "designers_min_products" in columns:
        op.drop_column("admin_ui_settings", "designers_min_products")


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "admin_ui_settings")

    if "designers_min_products" not in columns:
        op.add_column(
            "admin_ui_settings",
            sa.Column("designers_min_products", sa.Integer(), nullable=False, server_default="1"),
        )
        op.alter_column("admin_ui_settings", "designers_min_products", server_default=None)

    if "designers_exclude_store_names" not in columns:
        op.add_column(
            "admin_ui_settings",
            sa.Column("designers_exclude_store_names", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
        op.alter_column("admin_ui_settings", "designers_exclude_store_names", server_default=None)
