"""Add the opt-in public title-cleaning setting for sources.

Revision ID: 0102_add_source_public_title_cleaning
Revises: 0101_slow_driew_shopify_catalog_pacing
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0102_add_source_public_title_cleaning"
down_revision = "0101_slow_driew_shopify_catalog_pacing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE source_settings SET description_mode = 'text' WHERE description_mode = 'html'")
    op.drop_constraint("ck_source_settings_description_mode", "source_settings", type_="check")
    op.create_check_constraint(
        "ck_source_settings_description_mode",
        "source_settings",
        "description_mode IN ('hidden', 'text')",
    )
    op.add_column(
        "source_settings",
        sa.Column("clean_public_titles", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.execute("UPDATE source_settings SET clean_public_titles = TRUE")


def downgrade() -> None:
    op.drop_column("source_settings", "clean_public_titles")
    op.drop_constraint("ck_source_settings_description_mode", "source_settings", type_="check")
    op.create_check_constraint(
        "ck_source_settings_description_mode",
        "source_settings",
        "description_mode IN ('hidden', 'text', 'html')",
    )
