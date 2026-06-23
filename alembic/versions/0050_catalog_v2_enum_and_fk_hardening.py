"""catalog v2 enum and fk hardening

Revision ID: 0050_catalog_v2_enum_and_fk_hardening
Revises: 0049_catalog_v2_source_and_variant_constraints
Create Date: 2026-06-20 12:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0050_catalog_v2_enum_and_fk_hardening"
down_revision = "0049_catalog_v2_source_and_variant_constraints"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _check_exists(bind, table_name: str, check_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(check.get("name") == check_name for check in inspector.get_check_constraints(table_name))


def _get_fk_by_column(bind, table_name: str, column_name: str) -> dict | None:
    inspector = sa.inspect(bind)
    for fk in inspector.get_foreign_keys(table_name):
        constrained = fk.get("constrained_columns") or []
        if constrained == [column_name]:
            return fk
    return None


def _create_check_if_missing(bind, table_name: str, check_name: str, sqltext: str) -> None:
    if _table_exists(bind, table_name) and not _check_exists(bind, table_name, check_name):
        op.create_check_constraint(check_name, table_name, sqltext)


def _drop_check_if_exists(bind, table_name: str, check_name: str) -> None:
    if _table_exists(bind, table_name) and _check_exists(bind, table_name, check_name):
        op.drop_constraint(check_name, table_name, type_="check")


def upgrade() -> None:
    bind = op.get_bind()

    _create_check_if_missing(bind, "products", "ck_products_gender", "gender IN ('male', 'female', 'unisex')")
    _create_check_if_missing(
        bind,
        "products",
        "ck_products_availability_mode",
        "availability_mode IN ('in_stock', 'by_order')",
    )
    _create_check_if_missing(
        bind,
        "products",
        "ck_products_lifecycle_status",
        "lifecycle_status IN ('active', 'merged')",
    )
    _create_check_if_missing(
        bind,
        "products",
        "ck_products_visibility_status",
        "visibility_status IN ('visible', 'hidden')",
    )

    _create_check_if_missing(
        bind,
        "product_listings",
        "ck_product_listings_orderability_status",
        "orderability_status IN ('orderable', 'sold_out', 'unavailable')",
    )
    _create_check_if_missing(
        bind,
        "product_listings",
        "ck_product_listings_ingest_mode",
        "ingest_mode IN ('sync', 'manual')",
    )
    _create_check_if_missing(
        bind,
        "product_listing_variants",
        "ck_product_listing_variants_currency_code",
        "currency_code IS NULL OR char_length(currency_code) = 3",
    )

    _create_check_if_missing(
        bind,
        "source_settings",
        "ck_source_settings_description_mode",
        "description_mode IN ('hidden', 'text', 'html')",
    )
    _create_check_if_missing(
        bind,
        "source_sync_state",
        "ck_source_sync_state_last_sync_status",
        "last_sync_status IS NULL OR last_sync_status IN ('success', 'partial', 'failed')",
    )
    _create_check_if_missing(
        bind,
        "suppliers",
        "ck_suppliers_provider_kind",
        "provider_kind IN ('main', 'alternate')",
    )
    _create_check_if_missing(
        bind,
        "sync_jobs",
        "ck_sync_jobs_trigger_kind",
        "trigger_kind IN ('manual', 'scheduled', 'retry')",
    )
    _create_check_if_missing(
        bind,
        "sync_jobs",
        "ck_sync_jobs_status",
        "status IN ('queued', 'running', 'completed', 'failed', 'canceled')",
    )
    _create_check_if_missing(
        bind,
        "sync_job_source_runs",
        "ck_sync_job_source_runs_status",
        "status IN ('queued', 'running', 'completed', 'failed', 'skipped')",
    )
    _create_check_if_missing(
        bind,
        "product_dedup_decisions",
        "ck_product_dedup_decisions_decision_kind",
        "decision_kind IN ('reject', 'merge')",
    )

    if _table_exists(bind, "products"):
        fk = _get_fk_by_column(bind, "products", "designer_id")
        if fk is not None and fk.get("options", {}).get("ondelete") != "RESTRICT":
            if fk.get("name"):
                op.drop_constraint(fk["name"], "products", type_="foreignkey")
            op.create_foreign_key(
                "fk_products_designer_id_designers",
                "products",
                "designers",
                ["designer_id"],
                ["id"],
                ondelete="RESTRICT",
            )


def downgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "products"):
        fk = _get_fk_by_column(bind, "products", "designer_id")
        if fk is not None and fk.get("options", {}).get("ondelete") != "SET NULL":
            if fk.get("name"):
                op.drop_constraint(fk["name"], "products", type_="foreignkey")
            op.create_foreign_key(
                "fk_products_designer_id_designers",
                "products",
                "designers",
                ["designer_id"],
                ["id"],
                ondelete="SET NULL",
            )

    _drop_check_if_exists(bind, "product_dedup_decisions", "ck_product_dedup_decisions_decision_kind")
    _drop_check_if_exists(bind, "sync_job_source_runs", "ck_sync_job_source_runs_status")
    _drop_check_if_exists(bind, "sync_jobs", "ck_sync_jobs_status")
    _drop_check_if_exists(bind, "sync_jobs", "ck_sync_jobs_trigger_kind")
    _drop_check_if_exists(bind, "suppliers", "ck_suppliers_provider_kind")
    _drop_check_if_exists(bind, "source_sync_state", "ck_source_sync_state_last_sync_status")
    _drop_check_if_exists(bind, "source_settings", "ck_source_settings_description_mode")
    _drop_check_if_exists(bind, "product_listing_variants", "ck_product_listing_variants_currency_code")
    _drop_check_if_exists(bind, "product_listings", "ck_product_listings_ingest_mode")
    _drop_check_if_exists(bind, "product_listings", "ck_product_listings_orderability_status")
    _drop_check_if_exists(bind, "products", "ck_products_visibility_status")
    _drop_check_if_exists(bind, "products", "ck_products_lifecycle_status")
    _drop_check_if_exists(bind, "products", "ck_products_availability_mode")
    _drop_check_if_exists(bind, "products", "ck_products_gender")
