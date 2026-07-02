"""add site content storage

Revision ID: 0080_add_site_content
Revises: 0079_split_image_asset_uniqueness_by_scope
Create Date: 2026-07-01 00:00:01.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "0080_add_site_content"
down_revision = "0079_split_image_asset_uniqueness_by_scope"
branch_labels = None
depends_on = None


def _table_exists(bind: Connection, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "site_about_settings"):
        op.create_table(
            "site_about_settings",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.execute(sa.text("INSERT INTO site_about_settings (id, body_text) VALUES (1, '') ON CONFLICT (id) DO NOTHING"))

    if not _table_exists(bind, "site_about_photos"):
        op.create_table(
            "site_about_photos",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("image_asset_id", sa.BigInteger(), sa.ForeignKey("image_assets.id", ondelete="CASCADE"), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("position", name="uq_site_about_photos_position"),
        )

    if not _table_exists(bind, "site_question_items"):
        op.create_table(
            "site_question_items",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("answer", sa.Text(), nullable=False, server_default=""),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("is_expanded_by_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("position", name="uq_site_question_items_position"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "site_question_items"):
        op.drop_table("site_question_items")
    if _table_exists(bind, "site_about_photos"):
        op.drop_table("site_about_photos")
    if _table_exists(bind, "site_about_settings"):
        op.drop_table("site_about_settings")
