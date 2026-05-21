"""Add snapshot payloads for dedup decisions.

Revision ID: 0033_dedup_decision_snapshots
Revises: 0032_admin_ui_auto_sync_runtime_fields
Create Date: 2026-05-21 21:20:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0033_dedup_decision_snapshots"
down_revision = "0032_admin_ui_auto_sync_runtime_fields"
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
    columns = _column_names(bind, "parser_dedup_decision")
    if "snapshot_payload" not in columns:
        op.add_column("parser_dedup_decision", sa.Column("snapshot_payload", sa.JSON(), nullable=True))
    if "restore_payload" not in columns:
        op.add_column("parser_dedup_decision", sa.Column("restore_payload", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind, "parser_dedup_decision")
    if "restore_payload" in columns:
        op.drop_column("parser_dedup_decision", "restore_payload")
    if "snapshot_payload" in columns:
        op.drop_column("parser_dedup_decision", "snapshot_payload")
