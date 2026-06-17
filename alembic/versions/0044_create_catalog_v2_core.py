"""create catalog v2 core tables

Revision ID: 0044_create_catalog_v2_core
Revises: 0043_drop_variant_source_url_from_json
Create Date: 2026-06-17 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0044_create_catalog_v2_core"
down_revision = "0043_drop_variant_source_url_from_json"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _create_index_if_missing(bind, name: str, table_name: str, columns: list[str], *, unique: bool = False) -> None:
    if _index_exists(bind, table_name, name):
        return
    op.create_index(name, table_name, columns, unique=unique)


def _foreign_key_exists(bind, table_name: str, fk_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(fk.get("name") == fk_name for fk in inspector.get_foreign_keys(table_name))


def upgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "product_images"):
        op.drop_table("product_images")
    if _table_exists(bind, "products") and _has_column(bind, "products", "site_id"):
        op.drop_table("products")
    if _table_exists(bind, "sites"):
        op.drop_table("sites")

    if not _table_exists(bind, "designers"):
        op.create_table(
            "designers",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("slug", sa.String(length=255), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    if not _table_exists(bind, "sources"):
        op.create_table(
            "sources",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("key", sa.String(length=255), nullable=False, unique=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("base_url", sa.String(length=2048), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    if not _table_exists(bind, "suppliers"):
        op.create_table(
            "suppliers",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("key", sa.String(length=64), nullable=False, unique=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("provider_kind", sa.String(length=16), nullable=False, server_default=sa.text("'main'")),
            sa.Column("parent_supplier_id", sa.BigInteger(), sa.ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=True),
            sa.Column("rate_currency", sa.String(length=3), nullable=False, server_default=sa.text("'RUB'")),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        _create_index_if_missing(bind, "ix_suppliers_parent_supplier_id", "suppliers", ["parent_supplier_id"])

    if not _table_exists(bind, "pricing_settings"):
        op.create_table(
            "pricing_settings",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("markup_multiplier", sa.Numeric(10, 4), nullable=False, server_default=sa.text("1")),
            sa.Column("weight_tolerance", sa.Numeric(10, 4), nullable=False, server_default=sa.text("1")),
            sa.Column("customs_threshold_eur", sa.Numeric(12, 2), nullable=False, server_default=sa.text("200")),
            sa.Column("customs_duty_rate", sa.Numeric(10, 4), nullable=False, server_default=sa.text("0.15")),
            sa.Column("eur_to_rub_rate", sa.Numeric(12, 4), nullable=False, server_default=sa.text("105")),
            sa.Column("usd_to_rub_rate", sa.Numeric(12, 4), nullable=False, server_default=sa.text("95")),
            sa.Column("usdt_to_rub_rate", sa.Numeric(12, 4), nullable=False, server_default=sa.text("95")),
            sa.Column("usdt_extra_rub", sa.Numeric(12, 4), nullable=False, server_default=sa.text("1")),
            sa.Column("payment_fee_rate", sa.Numeric(10, 4), nullable=False, server_default=sa.text("0.02")),
            sa.Column("customs_processing_rate", sa.Numeric(10, 4), nullable=False, server_default=sa.text("0.08")),
            sa.Column("customs_fixed_rub", sa.Numeric(12, 2), nullable=False, server_default=sa.text("540")),
            sa.Column("tax_rate", sa.Numeric(10, 4), nullable=False, server_default=sa.text("0.06")),
            sa.Column("final_rounding_mode", sa.String(length=32), nullable=False, server_default=sa.text("'unit'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    if not _table_exists(bind, "weight_rules"):
        op.create_table(
            "weight_rules",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("weight_grams", sa.Integer(), nullable=False),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    if not _table_exists(bind, "image_assets"):
        op.create_table(
            "image_assets",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("storage_key", sa.String(length=2048), nullable=False),
            sa.Column("mime_type", sa.String(length=255), nullable=False),
            sa.Column("byte_size", sa.BigInteger(), nullable=False),
            sa.Column("width_px", sa.Integer(), nullable=True),
            sa.Column("height_px", sa.Integer(), nullable=True),
            sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("checksum_sha256", name="uq_image_assets_checksum_sha256"),
        )

    if not _table_exists(bind, "showcase_settings"):
        op.create_table(
            "showcase_settings",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("hero_image_asset_id", sa.BigInteger(), sa.ForeignKey("image_assets.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    if not _table_exists(bind, "sync_jobs"):
        op.create_table(
            "sync_jobs",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("trigger_kind", sa.String(length=16), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("triggered_by_admin_user_id", sa.Integer(), sa.ForeignKey("admin_user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("total_sources", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("processed_sources", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("products_seen", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("products_applied", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("error_message", sa.Text(), nullable=True),
        )
        _create_index_if_missing(bind, "ix_sync_jobs_triggered_by_admin_user_id", "sync_jobs", ["triggered_by_admin_user_id"])

    if not _table_exists(bind, "designer_source_names"):
        op.create_table(
            "designer_source_names",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("designer_id", sa.BigInteger(), sa.ForeignKey("designers.id", ondelete="SET NULL"), nullable=True),
            sa.Column("source_name", sa.String(length=255), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        _create_index_if_missing(bind, "ix_designer_source_names_designer_id", "designer_source_names", ["designer_id"])

    if not _table_exists(bind, "source_settings"):
        op.create_table(
            "source_settings",
            sa.Column("source_id", sa.BigInteger(), sa.ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("is_sync_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("hide_auto_added_products", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("description_mode", sa.String(length=16), nullable=False, server_default=sa.text("'text'")),
            sa.Column("show_images", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("promo_factor", sa.Numeric(10, 4), nullable=False, server_default=sa.text("1")),
            sa.Column("promo_only_no_discount", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("buyout_surcharge_value", sa.Numeric(12, 2), nullable=True),
            sa.Column("buyout_surcharge_currency", sa.String(length=3), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        _create_index_if_missing(bind, "ix_source_settings_supplier_id", "source_settings", ["supplier_id"])

    if not _table_exists(bind, "source_sync_state"):
        op.create_table(
            "source_sync_state",
            sa.Column("source_id", sa.BigInteger(), sa.ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_sync_duration_sec", sa.Integer(), nullable=True),
            sa.Column("last_sync_status", sa.String(length=32), nullable=True),
            sa.Column("last_error_code", sa.String(length=255), nullable=True),
            sa.Column("last_error_message", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    if not _table_exists(bind, "supplier_shipping_rates"):
        op.create_table(
            "supplier_shipping_rates",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("min_weight_kg", sa.Numeric(10, 3), nullable=False),
            sa.Column("max_weight_kg", sa.Numeric(10, 3), nullable=True),
            sa.Column("price_rub", sa.Numeric(12, 2), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        _create_index_if_missing(bind, "ix_supplier_shipping_rates_supplier_id", "supplier_shipping_rates", ["supplier_id"])

    if not _table_exists(bind, "weight_rule_keywords"):
        op.create_table(
            "weight_rule_keywords",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("rule_id", sa.BigInteger(), sa.ForeignKey("weight_rules.id", ondelete="CASCADE"), nullable=False),
            sa.Column("keyword", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("rule_id", "keyword", name="uq_weight_rule_keyword"),
        )
        _create_index_if_missing(bind, "ix_weight_rule_keywords_rule_id", "weight_rule_keywords", ["rule_id"])

    if not _table_exists(bind, "showcase_carousel_images"):
        op.create_table(
            "showcase_carousel_images",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("image_asset_id", sa.BigInteger(), sa.ForeignKey("image_assets.id", ondelete="CASCADE"), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("position", name="uq_showcase_carousel_images_position"),
        )

    if not _table_exists(bind, "products"):
        op.create_table(
            "products",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("designer_id", sa.BigInteger(), sa.ForeignKey("designers.id", ondelete="SET NULL"), nullable=True),
            sa.Column("primary_listing_id", sa.BigInteger(), nullable=True),
            sa.Column("gender", sa.String(length=16), nullable=False, server_default=sa.text("'unisex'")),
            sa.Column("availability_mode", sa.String(length=16), nullable=False, server_default=sa.text("'by_order'")),
            sa.Column("manual_weight_grams", sa.Integer(), nullable=True),
            sa.Column("weight_rule_id", sa.BigInteger(), sa.ForeignKey("weight_rules.id", ondelete="SET NULL"), nullable=True),
            sa.Column("lifecycle_status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")),
            sa.Column("visibility_status", sa.String(length=16), nullable=False, server_default=sa.text("'visible'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        _create_index_if_missing(bind, "ix_products_designer_id", "products", ["designer_id"])
        _create_index_if_missing(bind, "ix_products_primary_listing_id", "products", ["primary_listing_id"])
        _create_index_if_missing(bind, "ix_products_weight_rule_id", "products", ["weight_rule_id"])

    if not _table_exists(bind, "product_listings"):
        op.create_table(
            "product_listings",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("source_id", sa.BigInteger(), sa.ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("external_id", sa.String(length=255), nullable=True),
            sa.Column("url", sa.String(length=2048), nullable=False),
            sa.Column("handle", sa.String(length=1024), nullable=True),
            sa.Column("source_title", sa.String(length=2048), nullable=False),
            sa.Column("source_description_html", sa.Text(), nullable=True),
            sa.Column("source_description_text", sa.Text(), nullable=True),
            sa.Column("source_weight_grams", sa.Integer(), nullable=True),
            sa.Column("source_designer_raw", sa.String(length=255), nullable=True),
            sa.Column("source_category_raw", sa.String(length=255), nullable=True),
            sa.Column("orderability_status", sa.String(length=16), nullable=False, server_default=sa.text("'orderable'")),
            sa.Column("status_reason", sa.String(length=255), nullable=True),
            sa.Column("ingest_mode", sa.String(length=16), nullable=False, server_default=sa.text("'sync'")),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("source_id", "external_id", name="uq_product_listings_source_external_id"),
            sa.UniqueConstraint("source_id", "url", name="uq_product_listings_source_url"),
        )
        _create_index_if_missing(bind, "ix_product_listings_source_id", "product_listings", ["source_id"])
        _create_index_if_missing(bind, "idx_product_listings_last_seen_at", "product_listings", ["last_seen_at"])
        _create_index_if_missing(bind, "idx_product_listings_last_synced_at", "product_listings", ["last_synced_at"])

    if not _table_exists(bind, "product_listing_members"):
        op.create_table(
            "product_listing_members",
            sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("listing_id", sa.BigInteger(), sa.ForeignKey("product_listings.id", ondelete="CASCADE"), primary_key=True),
            sa.UniqueConstraint("listing_id", name="uq_product_listing_members_listing_id"),
        )

    if not _table_exists(bind, "product_listing_variants"):
        op.create_table(
            "product_listing_variants",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("listing_id", sa.BigInteger(), sa.ForeignKey("product_listings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("source_ref_id", sa.String(length=255), nullable=True),
            sa.Column("sku", sa.String(length=255), nullable=True),
            sa.Column("title", sa.String(length=1024), nullable=False),
            sa.Column("price_amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("compare_at_price_amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("currency_code", sa.String(length=3), nullable=True),
            sa.Column("is_orderable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("listing_id", "position", name="uq_product_listing_variants_listing_position"),
        )
        _create_index_if_missing(bind, "ix_product_listing_variants_listing_id", "product_listing_variants", ["listing_id"])
        _create_index_if_missing(bind, "idx_product_listing_variants_listing_source_ref_id", "product_listing_variants", ["listing_id", "source_ref_id"])

    if not _table_exists(bind, "product_listing_images"):
        op.create_table(
            "product_listing_images",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("listing_id", sa.BigInteger(), sa.ForeignKey("product_listings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("url", sa.String(length=2048), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("listing_id", "position", name="uq_product_listing_images_listing_position"),
        )
        _create_index_if_missing(bind, "ix_product_listing_images_listing_id", "product_listing_images", ["listing_id"])

    if not _table_exists(bind, "product_presentation"):
        op.create_table(
            "product_presentation",
            sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("title_override", sa.Text(), nullable=True),
            sa.Column("description_text", sa.Text(), nullable=True),
            sa.Column("description_html", sa.Text(), nullable=True),
            sa.Column("description_visibility", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    if not _table_exists(bind, "product_price_overrides"):
        op.create_table(
            "product_price_overrides",
            sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("manual_price_rub", sa.Numeric(12, 2), nullable=False),
            sa.Column("manual_compare_at_price_rub", sa.Numeric(12, 2), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    if not _table_exists(bind, "product_listing_gallery_images"):
        op.create_table(
            "product_listing_gallery_images",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
            sa.Column("listing_id", sa.BigInteger(), sa.ForeignKey("product_listings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("listing_image_id", sa.BigInteger(), sa.ForeignKey("product_listing_images.id", ondelete="SET NULL"), nullable=True),
            sa.Column("image_asset_id", sa.BigInteger(), sa.ForeignKey("image_assets.id", ondelete="SET NULL"), nullable=True),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("origin_kind", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("product_id", "listing_id", "position", name="uq_product_listing_gallery_images_scope_position"),
        )
        _create_index_if_missing(bind, "ix_product_listing_gallery_images_product_id", "product_listing_gallery_images", ["product_id"])
        _create_index_if_missing(bind, "ix_product_listing_gallery_images_listing_id", "product_listing_gallery_images", ["listing_id"])

    if not _table_exists(bind, "sync_job_source_runs"):
        op.create_table(
            "sync_job_source_runs",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("sync_job_id", sa.BigInteger(), sa.ForeignKey("sync_jobs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_id", sa.BigInteger(), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("products_received", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("products_applied", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("failed_products", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("error_code", sa.String(length=255), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
        )
        _create_index_if_missing(bind, "ix_sync_job_source_runs_sync_job_id", "sync_job_source_runs", ["sync_job_id"])
        _create_index_if_missing(bind, "ix_sync_job_source_runs_source_id", "sync_job_source_runs", ["source_id"])

    if not _table_exists(bind, "sync_applied_batches"):
        op.create_table(
            "sync_applied_batches",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("source_run_id", sa.BigInteger(), sa.ForeignKey("sync_job_source_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("batch_key", sa.String(length=255), nullable=False),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("source_run_id", "batch_key", name="uq_sync_applied_batches_source_run_batch_key"),
        )
        _create_index_if_missing(bind, "ix_sync_applied_batches_source_run_id", "sync_applied_batches", ["source_run_id"])
        _create_index_if_missing(bind, "idx_sync_applied_batches_applied_at", "sync_applied_batches", ["applied_at"])

    if not _table_exists(bind, "product_dedup_decisions"):
        op.create_table(
            "product_dedup_decisions",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("decision_kind", sa.String(length=16), nullable=False),
            sa.Column("created_product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        _create_index_if_missing(bind, "ix_product_dedup_decisions_created_product_id", "product_dedup_decisions", ["created_product_id"])

    if not _table_exists(bind, "product_dedup_decision_members"):
        op.create_table(
            "product_dedup_decision_members",
            sa.Column("decision_id", sa.BigInteger(), sa.ForeignKey("product_dedup_decisions.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
        )

    if _table_exists(bind, "products") and _table_exists(bind, "product_listings"):
        if not _foreign_key_exists(bind, "products", "fk_products_primary_listing_id_product_listings"):
            op.create_foreign_key(
                "fk_products_primary_listing_id_product_listings",
                "products",
                "product_listings",
                ["primary_listing_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "product_dedup_decision_members"):
        op.drop_table("product_dedup_decision_members")
    if _table_exists(bind, "product_dedup_decisions"):
        op.drop_index("ix_product_dedup_decisions_created_product_id", table_name="product_dedup_decisions")
        op.drop_table("product_dedup_decisions")
    if _table_exists(bind, "sync_applied_batches"):
        op.drop_index("idx_sync_applied_batches_applied_at", table_name="sync_applied_batches")
        op.drop_index("ix_sync_applied_batches_source_run_id", table_name="sync_applied_batches")
        op.drop_table("sync_applied_batches")
    if _table_exists(bind, "sync_job_source_runs"):
        op.drop_index("ix_sync_job_source_runs_source_id", table_name="sync_job_source_runs")
        op.drop_index("ix_sync_job_source_runs_sync_job_id", table_name="sync_job_source_runs")
        op.drop_table("sync_job_source_runs")
    if _table_exists(bind, "product_listing_gallery_images"):
        op.drop_index("ix_product_listing_gallery_images_listing_id", table_name="product_listing_gallery_images")
        op.drop_index("ix_product_listing_gallery_images_product_id", table_name="product_listing_gallery_images")
        op.drop_table("product_listing_gallery_images")
    if _table_exists(bind, "product_price_overrides"):
        op.drop_table("product_price_overrides")
    if _table_exists(bind, "product_presentation"):
        op.drop_table("product_presentation")
    if _table_exists(bind, "product_listing_images"):
        op.drop_index("ix_product_listing_images_listing_id", table_name="product_listing_images")
        op.drop_table("product_listing_images")
    if _table_exists(bind, "product_listing_variants"):
        op.drop_index("idx_product_listing_variants_listing_source_ref_id", table_name="product_listing_variants")
        op.drop_index("ix_product_listing_variants_listing_id", table_name="product_listing_variants")
        op.drop_table("product_listing_variants")
    if _table_exists(bind, "product_listing_members"):
        op.drop_table("product_listing_members")
    if _table_exists(bind, "product_listings"):
        op.drop_index("idx_product_listings_last_synced_at", table_name="product_listings")
        op.drop_index("idx_product_listings_last_seen_at", table_name="product_listings")
        op.drop_index("ix_product_listings_source_id", table_name="product_listings")
        op.drop_table("product_listings")
    if _table_exists(bind, "products"):
        try:
            op.drop_constraint("fk_products_primary_listing_id_product_listings", "products", type_="foreignkey")
        except Exception:
            pass
        op.drop_index("ix_products_weight_rule_id", table_name="products")
        op.drop_index("ix_products_primary_listing_id", table_name="products")
        op.drop_index("ix_products_designer_id", table_name="products")
        op.drop_table("products")
    if _table_exists(bind, "showcase_carousel_images"):
        op.drop_table("showcase_carousel_images")
    if _table_exists(bind, "weight_rule_keywords"):
        op.drop_index("ix_weight_rule_keywords_rule_id", table_name="weight_rule_keywords")
        op.drop_table("weight_rule_keywords")
    if _table_exists(bind, "supplier_shipping_rates"):
        op.drop_index("ix_supplier_shipping_rates_supplier_id", table_name="supplier_shipping_rates")
        op.drop_table("supplier_shipping_rates")
    if _table_exists(bind, "source_sync_state"):
        op.drop_table("source_sync_state")
    if _table_exists(bind, "source_settings"):
        op.drop_index("ix_source_settings_supplier_id", table_name="source_settings")
        op.drop_table("source_settings")
    if _table_exists(bind, "designer_source_names"):
        op.drop_index("ix_designer_source_names_designer_id", table_name="designer_source_names")
        op.drop_table("designer_source_names")
    if _table_exists(bind, "sync_jobs"):
        op.drop_index("ix_sync_jobs_triggered_by_admin_user_id", table_name="sync_jobs")
        op.drop_table("sync_jobs")
    if _table_exists(bind, "showcase_settings"):
        op.drop_table("showcase_settings")
    if _table_exists(bind, "image_assets"):
        op.drop_table("image_assets")
    if _table_exists(bind, "weight_rules"):
        op.drop_table("weight_rules")
    if _table_exists(bind, "pricing_settings"):
        op.drop_table("pricing_settings")
    if _table_exists(bind, "suppliers"):
        op.drop_index("ix_suppliers_parent_supplier_id", table_name="suppliers")
        op.drop_table("suppliers")
    if _table_exists(bind, "sources"):
        op.drop_table("sources")
    if _table_exists(bind, "designers"):
        op.drop_table("designers")
