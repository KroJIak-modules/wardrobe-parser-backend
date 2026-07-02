"""make site notifications a list

Revision ID: 0091_make_site_notifications_list
Revises: 0090_add_site_notification_content
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "0091_make_site_notifications_list"
down_revision = "0090_add_site_notification_content"
branch_labels = None
depends_on = None


def _column_exists(bind: Connection, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _table_exists(bind: Connection, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "site_notification_history_items"):
        op.drop_table("site_notification_history_items")

    if not _column_exists(bind, "site_notification_settings", "deleted_at"):
        op.add_column("site_notification_settings", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    bind.execute(
        sa.text(
            """
            DELETE FROM site_notification_settings
            WHERE title = 'ОБНОВЛЕНИЯ И НАХОДКИ'
              AND description = 'Следите за новостями модной индустрии и любимых брендов вместе со мной'
              AND button_text = 'ПЕРЕЙТИ В TELEGRAM'
              AND button_url = 'https://t.me/antonshellog'
              AND image_asset_id IS NULL
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, "site_notification_settings", "deleted_at"):
        op.drop_column("site_notification_settings", "deleted_at")
