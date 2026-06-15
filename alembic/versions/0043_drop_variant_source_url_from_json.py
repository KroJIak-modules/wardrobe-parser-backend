"""drop variant-level source_url from parser product json payloads

Revision ID: 0043_drop_variant_source_url_from_json
Revises: 0042_parser_product_variant_payload_cleanup
Create Date: 2026-06-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "0043_drop_variant_source_url_from_json"
down_revision = "0042_parser_product_variant_payload_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE parser_product_origin_variant
        SET payload = (
            COALESCE(payload::jsonb, '{}'::jsonb) - 'source_url'
        )::json
        """
    )

    op.execute(
        """
        UPDATE parser_product AS p
        SET variants = COALESCE(
            (
                SELECT jsonb_agg(elem - 'source_url')
                FROM jsonb_array_elements(COALESCE(p.variants::jsonb, '[]'::jsonb)) AS elem
            ),
            '[]'::jsonb
        )::json
        """
    )


def downgrade() -> None:
    # One-way cleanup of materialized JSON snapshots.
    pass
