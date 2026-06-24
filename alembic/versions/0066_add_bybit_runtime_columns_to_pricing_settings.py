"""add bybit runtime columns to pricing settings

Revision ID: 0066_add_bybit_runtime_columns_to_pricing_settings
Revises: 0065_move_logo_from_designers_to_sources
Create Date: 2026-06-23 02:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection


revision = "0066_add_bybit_runtime_columns_to_pricing_settings"
down_revision = "0065_move_logo_from_designers_to_sources"
branch_labels = None
depends_on = None


def _columns(bind: Connection, table_name: str) -> set[str]:
    return {str(column.get("name") or "").strip() for column in inspect(bind).get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "pricing_settings")

    if "bybit_bucket_rates" not in columns:
        op.add_column(
            "pricing_settings",
            sa.Column(
                "bybit_bucket_rates",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )
    if "bybit_last_updated_at" not in columns:
        op.add_column("pricing_settings", sa.Column("bybit_last_updated_at", sa.DateTime(timezone=True), nullable=True))
    if "bybit_last_error" not in columns:
        op.add_column("pricing_settings", sa.Column("bybit_last_error", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "pricing_settings")

    if "bybit_last_error" in columns:
        op.drop_column("pricing_settings", "bybit_last_error")
    if "bybit_last_updated_at" in columns:
        op.drop_column("pricing_settings", "bybit_last_updated_at")
    if "bybit_bucket_rates" in columns:
        op.drop_column("pricing_settings", "bybit_bucket_rates")
