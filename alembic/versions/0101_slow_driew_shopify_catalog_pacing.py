"""Slow the one Shopify catalogue that needs a calmer request cadence.

Revision ID: 0101_slow_driew_shopify_catalog_pacing
Revises: 0100_tune_shopify_catalog_sync_profiles
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0101_slow_driew_shopify_catalog_pacing"
down_revision = "0100_tune_shopify_catalog_sync_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE sources
            SET parser_config = jsonb_set(
                jsonb_set(
                    jsonb_set(
                        parser_config,
                        '{shopify_json_quality,page_interval_sec}',
                        '1.0'::jsonb,
                        true
                    ),
                    '{shopify_json_quality,antibot_pause_sec}',
                    '12'::jsonb,
                    true
                ),
                '{shopify_json_quality,retry_backoff_sec}',
                '[12, 24, 48]'::jsonb,
                true
            )
            WHERE key = 'driewgarments.com'
            """
        )
    )


def downgrade() -> None:
    # The measured source-specific pacing should not be removed automatically.
    pass
