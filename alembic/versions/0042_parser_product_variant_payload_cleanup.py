"""cleanup legacy parser product variant json payload fields

Revision ID: 0042_parser_product_variant_payload_cleanup
Revises: 0041_parser_origin_variant_source_url
Create Date: 2026-06-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "0042_parser_product_variant_payload_cleanup"
down_revision = "0041_parser_origin_variant_source_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE parser_product_origin_variant
        SET payload = (
            jsonb_strip_nulls(
                (COALESCE(payload::jsonb, '{}'::jsonb) - 'source_product_url' - 'source_variant_id' - 'source_variant_sku' - 'source_variant_title')
                || jsonb_build_object(
                    'source_url',
                    COALESCE(
                        NULLIF(COALESCE(payload::jsonb, '{}'::jsonb)->>'source_url', ''),
                        NULLIF(COALESCE(payload::jsonb, '{}'::jsonb)->>'source_product_url', ''),
                        NULLIF(source_url, '')
                    ),
                    'source_ref',
                    jsonb_strip_nulls(
                        COALESCE(COALESCE(payload::jsonb, '{}'::jsonb)->'source_ref', '{}'::jsonb)
                        || jsonb_build_object(
                            'id',
                            COALESCE(
                                NULLIF(COALESCE(payload::jsonb, '{}'::jsonb)->'source_ref'->>'id', ''),
                                NULLIF(COALESCE(payload::jsonb, '{}'::jsonb)->>'source_variant_id', ''),
                                NULLIF(source_variant_id, '')
                            ),
                            'sku',
                            COALESCE(
                                NULLIF(COALESCE(payload::jsonb, '{}'::jsonb)->'source_ref'->>'sku', ''),
                                NULLIF(COALESCE(payload::jsonb, '{}'::jsonb)->>'source_variant_sku', ''),
                                NULLIF(COALESCE(payload::jsonb, '{}'::jsonb)->>'sku', ''),
                                NULLIF(sku, '')
                            )
                        )
                    )
                )
            )
        )::json
        """
    )

    op.execute(
        """
        UPDATE parser_product AS p
        SET variants = COALESCE(
            (
                SELECT jsonb_agg(
                    jsonb_strip_nulls(
                        (elem - 'source_product_url' - 'source_variant_id' - 'source_variant_sku' - 'source_variant_title')
                        || jsonb_build_object(
                            'source_url',
                            COALESCE(
                                NULLIF(elem->>'source_url', ''),
                                NULLIF(elem->>'source_product_url', '')
                            ),
                            'source_ref',
                            jsonb_strip_nulls(
                                COALESCE(elem->'source_ref', '{}'::jsonb)
                                || jsonb_build_object(
                                    'id',
                                    COALESCE(
                                        NULLIF(elem->'source_ref'->>'id', ''),
                                        NULLIF(elem->>'source_variant_id', ''),
                                        NULLIF(elem->>'id', '')
                                    ),
                                    'sku',
                                    COALESCE(
                                        NULLIF(elem->'source_ref'->>'sku', ''),
                                        NULLIF(elem->>'source_variant_sku', ''),
                                        NULLIF(elem->>'sku', '')
                                    )
                                )
                            )
                        )
                    )
                )
                FROM jsonb_array_elements(COALESCE(p.variants::jsonb, '[]'::jsonb)) AS elem
            ),
            '[]'::jsonb
        )::json
        """
    )


def downgrade() -> None:
    # JSON cleanup is intentionally one-way.
    pass
