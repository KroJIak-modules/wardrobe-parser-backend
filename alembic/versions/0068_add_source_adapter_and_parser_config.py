"""add adapter key and parser config to sources

Revision ID: 0068_add_source_adapter_and_parser_config
Revises: 0067_drop_unused_designer_admin_ui_fields
Create Date: 2026-06-24 10:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0068_add_source_adapter_and_parser_config"
down_revision = "0067_drop_unused_designer_admin_ui_fields"
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "sources", "adapter_key"):
        op.add_column("sources", sa.Column("adapter_key", sa.String(length=255), nullable=True))
    if not _has_column(bind, "sources", "parser_config"):
        op.add_column(
            "sources",
            sa.Column(
                "parser_config",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "sources", "parser_config"):
        op.drop_column("sources", "parser_config")
    if _has_column(bind, "sources", "adapter_key"):
        op.drop_column("sources", "adapter_key")
