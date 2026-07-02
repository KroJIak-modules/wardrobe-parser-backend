"""Rename sale showcase category to Russian.

Revision ID: 0083_sale_category_title_ru
Revises: 0082_showcase_category_attachment_rules
Create Date: 2026-07-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0083_sale_category_title_ru"
down_revision = "0082_showcase_category_attachment_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE showcase_categories SET title = 'Скидки' WHERE code = 'sale'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE showcase_categories SET title = 'Sale' WHERE code = 'sale'"
        )
    )
