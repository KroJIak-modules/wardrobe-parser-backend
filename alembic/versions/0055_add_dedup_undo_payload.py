"""add dedup undo payload

Revision ID: 0055_add_dedup_undo_payload
Revises: 0054_rename_admin_ui_exclude_store_column
Create Date: 2026-06-20 08:50:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0055_add_dedup_undo_payload"
down_revision = "0054_rename_admin_ui_exclude_store_column"
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "product_dedup_decisions", "undo_payload"):
        op.add_column("product_dedup_decisions", sa.Column("undo_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "product_dedup_decisions", "undo_payload"):
        op.drop_column("product_dedup_decisions", "undo_payload")
