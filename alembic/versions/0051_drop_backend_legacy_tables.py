"""drop backend legacy tables

Revision ID: 0051_drop_backend_legacy_tables
Revises: 0050_catalog_v2_enum_and_fk_hardening
Create Date: 2026-06-20 12:30:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "0051_drop_backend_legacy_tables"
down_revision = "0050_catalog_v2_enum_and_fk_hardening"
branch_labels = None
depends_on = None


_LEGACY_TABLES = [
    "parser_product_origin_variant",
    "parser_product_fingerprint",
    "parser_product_delta",
    "parser_product_category_match",
    "parser_category_manual_product",
    "parser_category_keyword",
    "parser_category_count_snapshot",
    "parser_job_source_run",
    "parser_favorite_product",
    "parser_dedup_decision",
    "parser_brand_mapping",
    "parser_weight_keyword",
    "parser_weight_rule",
    "parser_supplier_shipping_rate",
    "parser_supplier",
    "parser_pricing_settings",
    "parser_job",
    "parser_category_index_state",
    "parser_category",
    "parser_product",
    "parser_source",
    "sync_applied_batch",
    "sync_job_runtime",
    "image_asset",
]


def upgrade() -> None:
    for table_name in _LEGACY_TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')


def downgrade() -> None:
    raise RuntimeError("Downgrade is not supported for dropped legacy tables.")
