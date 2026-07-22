"""Update Store Backlash to its current Shopify storefront.

Revision ID: 0099_update_store_backlash_to_shopify
Revises: 0098_add_filter_assignment_rebuild_progress
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0099_update_store_backlash_to_shopify"
down_revision = "0098_add_filter_assignment_rebuild_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE sources
            SET adapter_key = 'store_backlash__v1',
                parser_config = jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            parser_config - 'store_backlash_colorme_workers',
                            '{adapter_ready}',
                            'true'::jsonb,
                            true
                        ),
                        '{adapter_revision}',
                        '"store_backlash__v1-r2"'::jsonb,
                        true
                    ),
                    '{strategy_sequence}',
                    '["shopify_json", "shopify_js"]'::jsonb,
                    true
                )
                || jsonb_build_object(
                    'retry_limits', jsonb_build_object('shopify_json', 1, 'shopify_js', 1),
                    'shopify_sitemap', jsonb_build_object('max_products', 50000, 'request_retries', 3, 'include_locale_sitemaps', false),
                    'shopify_js_workers', 8,
                    'shopify_js_quality', jsonb_build_object('wait_log_sec', 15, 'pause_poll_sec', 0.2, 'progress_every', 25, 'antibot_pause_sec', 3, 'retry_backoff_sec', jsonb_build_array(1, 3)),
                    'shopify_json_quality', jsonb_build_object('antibot_pause_sec', 3, 'retry_backoff_sec', jsonb_build_array(1, 3, 7), 'enrich_from_js_fields', jsonb_build_array())
                )
            WHERE key = 'store-backlash.jp'
            """
        )
    )


def downgrade() -> None:
    # Storefront platform changes are intentionally not rolled back.
    pass
