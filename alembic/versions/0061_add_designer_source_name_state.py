"""add source designer mapping state fields

Revision ID: 0061_add_designer_source_name_state
Revises: 0060_add_dedup_last_run_fields_to_admin_ui_settings
Create Date: 2026-06-21 10:50:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0061_add_designer_source_name_state"
down_revision = "0060_add_dedup_last_run_fields_to_admin_ui_settings"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "designer_source_names"):
        return

    if not _has_column(bind, "designer_source_names", "designer_name"):
        op.add_column("designer_source_names", sa.Column("designer_name", sa.String(length=255), nullable=True))
    if not _has_column(bind, "designer_source_names", "is_enabled"):
        op.add_column(
            "designer_source_names",
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        )

    bind.execute(
        sa.text(
            """
            update designer_source_names dsn
            set
                designer_name = coalesce(nullif(btrim(d.name), ''), nullif(btrim(dsn.source_name), '')),
                is_enabled = true
            from designers d
            where dsn.designer_id = d.id
              and (dsn.designer_name is null or btrim(dsn.designer_name) = '')
            """
        )
    )
    bind.execute(
        sa.text(
            """
            update designer_source_names
            set
                designer_name = nullif(btrim(source_name), ''),
                is_enabled = true
            where designer_name is null or btrim(designer_name) = ''
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "designer_source_names"):
        return
    if _has_column(bind, "designer_source_names", "is_enabled"):
        op.drop_column("designer_source_names", "is_enabled")
    if _has_column(bind, "designer_source_names", "designer_name"):
        op.drop_column("designer_source_names", "designer_name")
