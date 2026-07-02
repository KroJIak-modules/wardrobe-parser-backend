"""remove truncated seed site notification

Revision ID: 0093_remove_truncated_seed_site_notification
Revises: 0092_remove_seed_site_notification
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "0093_remove_truncated_seed_site_notification"
down_revision = "0092_remove_seed_site_notification"
branch_labels = None
depends_on = None


def _table_exists(bind: Connection, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "site_notification_settings"):
        return

    bind.execute(
        sa.text(
            """
            DELETE FROM site_notification_settings
            WHERE button_url = 'https://t.me/antonshellog'
              AND button_text = 'ПЕРЕЙТИ В TELEGRAM'
              AND title LIKE 'ОБНОВЛЕНИЯ И НАХОДК%'
            """
        )
    )


def downgrade() -> None:
    pass
