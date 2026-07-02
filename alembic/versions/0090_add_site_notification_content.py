"""add site notification content

Revision ID: 0090_add_site_notification_content
Revises: 0089_add_weight_recalc_runtime_state
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "0090_add_site_notification_content"
down_revision = "0089_add_weight_recalc_runtime_state"
branch_labels = None
depends_on = None


def _table_exists(bind: Connection, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "site_notification_settings"):
        op.create_table(
            "site_notification_settings",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("title", sa.Text(), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("button_text", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("button_url", sa.String(length=2048), nullable=False, server_default=""),
            sa.Column("image_asset_id", sa.BigInteger(), sa.ForeignKey("image_assets.id", ondelete="SET NULL"), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("version >= 1", name="ck_site_notification_settings_version_positive"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "site_notification_settings"):
        op.drop_table("site_notification_settings")
