"""add designer lifecycle fields

Revision ID: 0062_add_designer_lifecycle_fields
Revises: 0061_add_designer_source_name_state
Create Date: 2026-06-21 13:20:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0062_add_designer_lifecycle_fields"
down_revision = "0061_add_designer_source_name_state"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _has_check_constraint(bind, table_name: str, constraint_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(item.get("name") == constraint_name for item in inspector.get_check_constraints(table_name))


def upgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "designers"):
        if not _has_column(bind, "designers", "origin_kind"):
            op.add_column(
                "designers",
                sa.Column("origin_kind", sa.String(length=16), nullable=False, server_default="manual"),
            )
        if not _has_column(bind, "designers", "is_admin_touched"):
            op.add_column(
                "designers",
                sa.Column("is_admin_touched", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            )
        bind.execute(
            sa.text(
                """
                update designers
                set
                    origin_kind = 'manual',
                    is_admin_touched = true
                """
            )
        )
        if not _has_check_constraint(bind, "designers", "ck_designers_origin_kind"):
            op.create_check_constraint(
                "ck_designers_origin_kind",
                "designers",
                "origin_kind IN ('auto', 'manual')",
            )

    if _table_exists(bind, "designer_source_names"):
        if not _has_column(bind, "designer_source_names", "is_admin_touched"):
            op.add_column(
                "designer_source_names",
                sa.Column("is_admin_touched", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            )
        bind.execute(sa.text("update designer_source_names set is_admin_touched = true"))


def downgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "designers"):
        if _has_check_constraint(bind, "designers", "ck_designers_origin_kind"):
            op.drop_constraint("ck_designers_origin_kind", "designers", type_="check")
        if _has_column(bind, "designers", "is_admin_touched"):
            op.drop_column("designers", "is_admin_touched")
        if _has_column(bind, "designers", "origin_kind"):
            op.drop_column("designers", "origin_kind")

    if _table_exists(bind, "designer_source_names") and _has_column(bind, "designer_source_names", "is_admin_touched"):
        op.drop_column("designer_source_names", "is_admin_touched")
