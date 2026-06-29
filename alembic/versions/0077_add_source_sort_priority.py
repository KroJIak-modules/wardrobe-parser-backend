"""add source sort priority

Revision ID: 0077_add_source_sort_priority
Revises: 0076_add_filter_mobile_menu_group_code
Create Date: 2026-06-29 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "0077_add_source_sort_priority"
down_revision = "0076_add_filter_mobile_menu_group_code"
branch_labels = None
depends_on = None


def _column_exists(bind: Connection, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "source_settings", "sort_priority"):
        op.add_column("source_settings", sa.Column("sort_priority", sa.Integer(), nullable=True))
    bind.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    s.id AS source_id,
                    row_number() OVER (
                        ORDER BY
                            CASE
                                WHEN s.key = 'manual.local' THEN 0
                                WHEN lower(coalesce(s.parser_config->>'mode', 'auto')) = 'manual' THEN 1
                                ELSE 2
                            END ASC,
                            lower(coalesce(s.name, s.key)) ASC,
                            s.id ASC
                    ) AS sort_priority
                FROM sources s
            )
            UPDATE source_settings ss
            SET sort_priority = ranked.sort_priority
            FROM ranked
            WHERE ss.source_id = ranked.source_id
              AND (ss.sort_priority IS NULL OR ss.sort_priority <> ranked.sort_priority)
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO source_settings (source_id, sort_priority)
            SELECT ranked.source_id, ranked.sort_priority
            FROM (
                SELECT
                    s.id AS source_id,
                    row_number() OVER (
                        ORDER BY
                            CASE
                                WHEN s.key = 'manual.local' THEN 0
                                WHEN lower(coalesce(s.parser_config->>'mode', 'auto')) = 'manual' THEN 1
                                ELSE 2
                            END ASC,
                            lower(coalesce(s.name, s.key)) ASC,
                            s.id ASC
                    ) AS sort_priority
                FROM sources s
            ) ranked
            LEFT JOIN source_settings ss ON ss.source_id = ranked.source_id
            WHERE ss.source_id IS NULL
            """
        )
    )
    op.alter_column("source_settings", "sort_priority", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, "source_settings", "sort_priority"):
        op.drop_column("source_settings", "sort_priority")
