"""Tune Shopify sources for paced catalogue syncs.

Revision ID: 0100_tune_shopify_catalog_sync_profiles
Revises: 0099_update_store_backlash_to_shopify
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0100_tune_shopify_catalog_sync_profiles"
down_revision = "0099_update_store_backlash_to_shopify"
branch_labels = None
depends_on = None


SHOPIFY_PAGE_INTERVALS = {
    "jadedldn.com": 0.25,
    "racerworldwide.net": 0.2,
    "nofaithstudios.com": 0.2,
    "professor-e.com": 0.2,
    "essxnyc.com": 0.3,
    "paradoxeparis.com": 0.2,
    "thelastconspiracy.com": 0.2,
    "julius-garden.online": 0.25,
    "14thaddiction.com": 0.2,
    "prayingg.com": 0.2,
    "fourtwofour.com": 0.2,
    "remnantsvintage.com": 0.2,
    "misssixty.com": 0.3,
    "hlorenzo.com": 0.35,
    "simonerocha.com": 0.25,
    "orimono.eu": 0.25,
    "pauleasterlin.com": 0.2,
    "driewgarments.com": 0.35,
    "junalyx.com": 0.2,
    "archived.co": 0.3,
    "store-backlash.jp": 0.4,
    "newrock.com": 0.35,
    "rickowens.eu": 0.35,
}

SHOPIFY_RECOVERY_PROFILES = {
    "driewgarments.com": {
        "antibot_pause_sec": 12,
        "retry_backoff_sec": [12, 24, 48],
    },
}


def upgrade() -> None:
    bind = op.get_bind()
    for source_key, page_interval_sec in SHOPIFY_PAGE_INTERVALS.items():
        bind.execute(
            sa.text(
                """
                UPDATE sources
                SET parser_config = jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            parser_config - 'shopify_js_workers' - 'shopify_js_quality',
                            '{strategy_sequence}',
                            '["shopify_json"]'::jsonb,
                            true
                        ),
                        '{retry_limits}',
                        '{"shopify_json": 1}'::jsonb,
                        true
                    ),
                    '{shopify_json_quality,page_interval_sec}',
                    to_jsonb(CAST(:page_interval_sec AS numeric)),
                    true
                )
                WHERE key = :source_key
                """
            ),
            {"source_key": source_key, "page_interval_sec": page_interval_sec},
        )
    bind.execute(
        sa.text(
            """
            UPDATE sources
            SET parser_config = jsonb_set(
                jsonb_set(parser_config, '{adapter_ready}', 'true'::jsonb, true),
                '{adapter_revision}',
                to_jsonb(adapter_key || '-r2'),
                true
            )
            WHERE key IN ('newrock.com', 'rickowens.eu')
            """
        )
    )

    for source_key, profile in SHOPIFY_RECOVERY_PROFILES.items():
        bind.execute(
            sa.text(
                """
                UPDATE sources
                SET parser_config = jsonb_set(
                    jsonb_set(
                        parser_config,
                        '{shopify_json_quality,antibot_pause_sec}',
                        to_jsonb(CAST(:antibot_pause_sec AS numeric)),
                        true
                    ),
                    '{shopify_json_quality,retry_backoff_sec}',
                    CAST(:retry_backoff_sec AS jsonb),
                    true
                )
                WHERE key = :source_key
                """
            ),
            {
                "source_key": source_key,
                "antibot_pause_sec": profile["antibot_pause_sec"],
                "retry_backoff_sec": '[12, 24, 48]',
            },
        )


def downgrade() -> None:
    # The previous JS fallback caused uncontrolled WAF amplification and is not restored.
    pass
