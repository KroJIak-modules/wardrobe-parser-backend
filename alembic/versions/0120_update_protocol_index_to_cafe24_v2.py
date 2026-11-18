"""switch protocol index to the cafe24 v2 adapter

Revision ID: 0120_update_protocol_index_to_cafe24_v2
Revises: 0119_add_site_sort_price_has_range
Create Date: 2026-11-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0120_update_protocol_index_to_cafe24_v2"
down_revision = "0119_add_site_sort_price_has_range"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE sources
            SET adapter_key = 'intlprotocolindex__v2',
                parser_config = jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            parser_config,
                            '{strategy_sequence}',
                            '["intl_protocol_index_cafe24_v2"]'::jsonb,
                            true
                        ),
                        '{retry_limits}',
                        jsonb_set(
                            COALESCE(parser_config -> 'retry_limits', '{}'::jsonb),
                            '{intl_protocol_index_cafe24_v2}',
                            '0'::jsonb,
                            true
                        ),
                        true
                    ),
                    '{adapter_revision}',
                    '"intlprotocolindex__v2-r1"'::jsonb,
                    true
                )
            WHERE key = 'intl-protocol-index.com'
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE sources
            SET adapter_key = 'intlprotocolindex__v1',
                parser_config = jsonb_set(
                    jsonb_set(
                        parser_config,
                        '{strategy_sequence}',
                        '["intl_protocol_index_cafe24"]'::jsonb,
                        true
                    ),
                    '{adapter_revision}',
                    '"intlprotocolindex__v1-r1"'::jsonb,
                    true
                )
            WHERE key = 'intl-protocol-index.com'
            """
        )
    )
