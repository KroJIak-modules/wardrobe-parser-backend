"""Enable public title cleaning for all existing and future sources.

Revision ID: 0103_enable_public_title_cleaning_by_default
Revises: 0102_add_source_public_title_cleaning
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0103_enable_public_title_cleaning_by_default"
down_revision = "0102_add_source_public_title_cleaning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("source_settings", "clean_public_titles", server_default=sa.true())
    op.execute("UPDATE source_settings SET clean_public_titles = TRUE")


def downgrade() -> None:
    op.alter_column("source_settings", "clean_public_titles", server_default=sa.false())
