"""add description_visible_override to parser_product

Revision ID: 0034_add_description_visible_override
Revises: 0033_dedup_decision_snapshots
Create Date: 2026-05-22 00:30:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "0034_add_description_visible_override"
down_revision = "0033_dedup_decision_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE parser_product ADD COLUMN IF NOT EXISTS description_visible_override BOOLEAN")


def downgrade() -> None:
    op.execute("ALTER TABLE parser_product DROP COLUMN IF EXISTS description_visible_override")

