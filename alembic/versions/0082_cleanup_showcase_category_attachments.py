"""Cleanup invalid showcase category attachments.

Revision ID: 0082_showcase_category_attachment_rules
Revises: 0081_filter_title_slugs
Create Date: 2026-07-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0082_showcase_category_attachment_rules"
down_revision = "0081_filter_title_slugs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM showcase_category_attachments AS attachment
            USING showcase_categories AS category
            WHERE attachment.showcase_category_id = category.id
              AND NOT (
                (category.code = 'new' AND attachment.attachment_kind = 'custom_catalog')
                OR
                (category.code IN ('men', 'women') AND attachment.attachment_kind = 'filter')
              )
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError("Downgrade is not supported for showcase category attachment cleanup")
