"""Add pricing svc rules.

Revision ID: 0085_add_pricing_svc_rules
Revises: 0084_add_site_sort_price_cache
Create Date: 2026-07-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection


revision = "0085_add_pricing_svc_rules"
down_revision = "0084_add_site_sort_price_cache"
branch_labels = None
depends_on = None


def _column_exists(bind: Connection, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "pricing_settings", "svc_rules"):
        op.add_column(
            "pricing_settings",
            sa.Column(
                "svc_rules",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, "pricing_settings", "svc_rules"):
        op.drop_column("pricing_settings", "svc_rules")
