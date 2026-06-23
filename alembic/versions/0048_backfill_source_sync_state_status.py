"""backfill source sync state status

Revision ID: 0048_backfill_source_sync_state_status
Revises: 0047_catalog_v2_hardening
Create Date: 2026-06-17 00:45:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0048_backfill_source_sync_state_status"
down_revision = "0047_catalog_v2_hardening"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "source_sync_state"):
        return
    op.execute(
        """
        UPDATE source_sync_state
        SET last_sync_status = CASE
            WHEN lower(coalesce(last_sync_status, '')) = 'completed' THEN 'success'
            WHEN lower(coalesce(last_sync_status, '')) IN ('queued', 'running', 'canceled', 'cancelled', 'skipped') THEN 'failed'
            ELSE last_sync_status
        END
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "source_sync_state"):
        return
    op.execute(
        """
        UPDATE source_sync_state
        SET last_sync_status = CASE
            WHEN lower(coalesce(last_sync_status, '')) = 'success' THEN 'completed'
            ELSE last_sync_status
        END
        """
    )
