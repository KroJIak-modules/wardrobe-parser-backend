"""add site access settings

Revision ID: 0096_add_site_access_settings
Revises: 0095_add_source_published_at_to_product_listings
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0096_add_site_access_settings"
down_revision = "0095_add_source_published_at_to_product_listings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_access_settings",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("title", sa.String(length=255), server_default="", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("password_value", sa.String(length=255), server_default="", nullable=False),
        sa.Column("password_hash", sa.String(length=512), server_default="", nullable=False),
        sa.Column("session_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("session_version >= 1", name="ck_site_access_settings_session_version_positive"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("site_access_settings")
