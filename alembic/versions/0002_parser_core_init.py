"""init parser core tables

Revision ID: 0002_parser_core_init
Revises: 0001_backend_init
Create Date: 2026-02-11 00:00:01.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "0002_parser_core_init"
down_revision = "0001_backend_init"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    return table_name in inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    product_status = postgresql.ENUM(
        "available",
        "out_of_stock",
        "hidden",
        "unavailable",
        name="productstatus",
    )
    product_status.create(bind, checkfirst=True)

    if not _table_exists(bind, "parser_supplier"):
        op.create_table(
            "parser_supplier",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("key", sa.String(length=64), nullable=False, unique=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("category", sa.String(length=16), nullable=False, server_default="main"),
            sa.Column("parent_supplier_id", sa.Integer(), sa.ForeignKey("parser_supplier.id", ondelete="CASCADE"), nullable=True),
            sa.Column("alt_position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("rate_currency", sa.String(length=3), nullable=False, server_default="RUB"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("idx_parser_supplier_key", "parser_supplier", ["key"], unique=False)
        op.create_index("idx_parser_supplier_category", "parser_supplier", ["category"], unique=False)

    if not _table_exists(bind, "parser_supplier_shipping_rate"):
        op.create_table(
            "parser_supplier_shipping_rate",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("parser_supplier.id", ondelete="CASCADE"), nullable=False),
            sa.Column("step_500g", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("rate_rub", sa.Float(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("supplier_id", "step_500g", name="uq_parser_supplier_shipping_rate_supplier_step"),
        )
        op.create_index("idx_parser_supplier_shipping_rate_supplier_id", "parser_supplier_shipping_rate", ["supplier_id"], unique=False)
        op.create_index("idx_parser_supplier_shipping_rate_step_500g", "parser_supplier_shipping_rate", ["step_500g"], unique=False)

    if not _table_exists(bind, "parser_source"):
        op.create_table(
            "parser_source",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("url", sa.String(length=2048), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("parser_supplier.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("promo_factor", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("promo_only_no_discount", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("buyout_surcharge_value", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("buyout_surcharge_currency", sa.String(length=3), nullable=False, server_default="RUB"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not _table_exists(bind, "parser_product"):
        op.create_table(
            "parser_product",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source_id", sa.Integer(), sa.ForeignKey("parser_source.id"), nullable=False),
            sa.Column("handle", sa.String(length=1024), nullable=False),
            sa.Column("title", sa.String(length=2048), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("vendor", sa.String(length=255), nullable=True),
            sa.Column("product_type", sa.String(length=255), nullable=True),
            sa.Column("url", sa.String(length=2048), nullable=False, unique=True),
            sa.Column("price", sa.Float(), nullable=True),
            sa.Column("currency", sa.String(length=3), nullable=True),
            sa.Column("status", product_status, nullable=False, server_default="available"),
            sa.Column("image_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("image_urls", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
            sa.Column("image_asset_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
            sa.Column("variants", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
            sa.Column("weight_grams", sa.Float(), nullable=True),
            sa.Column("weight_source", sa.String(length=32), nullable=True),
            sa.Column("weight_match_keyword", sa.String(length=255), nullable=True),
            sa.Column("weight_value", sa.Float(), nullable=True),
            sa.Column("weight_unit", sa.String(length=16), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not _table_exists(bind, "parser_category"):
        op.create_table(
            "parser_category",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("slug", sa.String(length=255), nullable=False),
            sa.Column("parent_id", sa.Integer(), sa.ForeignKey("parser_category.id"), nullable=True),
            sa.Column("is_fallback", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("slug", name="uq_parser_category_slug"),
        )
        op.create_index("idx_parser_category_parent_id", "parser_category", ["parent_id"], unique=False)
        op.create_index("idx_parser_category_deleted_at", "parser_category", ["deleted_at"], unique=False)
        op.create_index("idx_parser_category_is_fallback", "parser_category", ["is_fallback"], unique=False)
        op.create_index("idx_parser_category_is_favorite", "parser_category", ["is_favorite"], unique=False)
        op.create_index("idx_parser_category_is_enabled", "parser_category", ["is_enabled"], unique=False)

    if not _table_exists(bind, "parser_category_keyword"):
        op.create_table(
            "parser_category_keyword",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("category_id", sa.Integer(), sa.ForeignKey("parser_category.id", ondelete="CASCADE"), nullable=False),
            sa.Column("keyword", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("category_id", "keyword", name="uq_parser_category_keyword"),
        )
        op.create_index("idx_parser_category_keyword_category", "parser_category_keyword", ["category_id"], unique=False)
        op.create_index("idx_parser_category_keyword_keyword", "parser_category_keyword", ["keyword"], unique=False)

    if not _table_exists(bind, "parser_favorite_product"):
        op.create_table(
            "parser_favorite_product",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("product_id", sa.Integer(), sa.ForeignKey("parser_product.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("idx_parser_favorite_product_product_id", "parser_favorite_product", ["product_id"], unique=False)

    if not _table_exists(bind, "parser_dedup_decision"):
        op.create_table(
            "parser_dedup_decision",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("pair_key", sa.String(length=64), nullable=False, unique=True),
            sa.Column("left_product_id", sa.Integer(), sa.ForeignKey("parser_product.id"), nullable=False),
            sa.Column("right_product_id", sa.Integer(), sa.ForeignKey("parser_product.id"), nullable=False),
            sa.Column("action", sa.String(length=20), nullable=False),
            sa.Column("merged_into_product_id", sa.Integer(), sa.ForeignKey("parser_product.id"), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("idx_parser_dedup_decision_action", "parser_dedup_decision", ["action"], unique=False)
        op.create_index("idx_parser_dedup_decision_left", "parser_dedup_decision", ["left_product_id"], unique=False)
        op.create_index("idx_parser_dedup_decision_right", "parser_dedup_decision", ["right_product_id"], unique=False)

    if not _table_exists(bind, "parser_pricing_settings"):
        op.create_table(
            "parser_pricing_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("markup_multiplier", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("weight_tolerance", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("promo_factor", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("customs_threshold_eur", sa.Float(), nullable=False, server_default="200.0"),
            sa.Column("customs_threshold_currency", sa.String(length=3), nullable=False, server_default="EUR"),
            sa.Column("customs_duty_rate", sa.Float(), nullable=False, server_default="0.15"),
            sa.Column("usd_to_rub", sa.Float(), nullable=False, server_default="95.0"),
            sa.Column("eur_to_rub", sa.Float(), nullable=False, server_default="105.0"),
            sa.Column("bybit_usdt_to_rub", sa.Float(), nullable=False, server_default="95.0"),
            sa.Column("bybit_extra_rub", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("final_rounding_mode", sa.String(length=32), nullable=False, server_default="unit"),
            sa.Column("bybit_bucket_rates", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
            sa.Column("bybit_last_updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("bybit_last_error", sa.String(length=1024), nullable=True),
            sa.Column("eur_to_usd_rate", sa.Float(), nullable=False, server_default="1.18"),
            sa.Column("gbp_to_usd_rate", sa.Float(), nullable=False, server_default="1.4"),
            sa.Column("payment_fee_rate", sa.Float(), nullable=False, server_default="0.02"),
            sa.Column("customs_processing_rate", sa.Float(), nullable=False, server_default="0.08"),
            sa.Column("customs_fixed_rub", sa.Float(), nullable=False, server_default="540.0"),
            sa.Column("shipping_alt_threshold_eur", sa.Float(), nullable=False, server_default="300.0"),
            sa.Column("tax_rate", sa.Float(), nullable=False, server_default="0.06"),
            sa.Column("dedup_only_available_products", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("show_product_description", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("svc_rules", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
            sa.Column("insurance_rules", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
            sa.Column("service_fee_rules", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
            sa.Column("shipping_rules", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("showcase_hero_image_asset_id", sa.Integer(), nullable=True),
            sa.Column("showcase_carousel_image_asset_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("idx_parser_pricing_settings_updated_at", "parser_pricing_settings", ["updated_at"], unique=False)

    if not _table_exists(bind, "parser_weight_rule"):
        op.create_table(
            "parser_weight_rule",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("weight_grams", sa.Integer(), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_parser_weight_rule_deleted_at", "parser_weight_rule", ["deleted_at"], unique=False)
        op.create_index("idx_parser_weight_rule_weight_grams", "parser_weight_rule", ["weight_grams"], unique=False)

    if not _table_exists(bind, "parser_weight_keyword"):
        op.create_table(
            "parser_weight_keyword",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("rule_id", sa.Integer(), sa.ForeignKey("parser_weight_rule.id", ondelete="CASCADE"), nullable=False),
            sa.Column("keyword", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("rule_id", "keyword", name="uq_parser_weight_rule_keyword"),
        )
        op.create_index("idx_parser_weight_keyword_rule_id", "parser_weight_keyword", ["rule_id"], unique=False)
        op.create_index("idx_parser_weight_keyword_keyword", "parser_weight_keyword", ["keyword"], unique=False)

    if not _table_exists(bind, "image_asset"):
        op.create_table(
            "image_asset",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("storage_rel_path", sa.String(length=1024), nullable=False),
            sa.Column("mime_type", sa.String(length=128), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("width", sa.Integer(), nullable=True),
            sa.Column("height", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_image_asset_kind", "image_asset", ["kind"], unique=False)
        op.create_index("idx_image_asset_sha256", "image_asset", ["sha256"], unique=False)

    # Seed singleton defaults expected by app.
    op.execute(
        """
        INSERT INTO parser_pricing_settings (id)
        SELECT 1
        WHERE NOT EXISTS (SELECT 1 FROM parser_pricing_settings WHERE id = 1)
        """
    )


def downgrade() -> None:
    bind = op.get_bind()

    for idx_name, table_name in (
        ("idx_image_asset_sha256", "image_asset"),
        ("idx_image_asset_kind", "image_asset"),
        ("idx_parser_weight_keyword_keyword", "parser_weight_keyword"),
        ("idx_parser_weight_keyword_rule_id", "parser_weight_keyword"),
        ("idx_parser_weight_rule_weight_grams", "parser_weight_rule"),
        ("idx_parser_weight_rule_deleted_at", "parser_weight_rule"),
        ("idx_parser_pricing_settings_updated_at", "parser_pricing_settings"),
        ("idx_parser_dedup_decision_right", "parser_dedup_decision"),
        ("idx_parser_dedup_decision_left", "parser_dedup_decision"),
        ("idx_parser_dedup_decision_action", "parser_dedup_decision"),
        ("idx_parser_favorite_product_product_id", "parser_favorite_product"),
        ("idx_parser_category_keyword_keyword", "parser_category_keyword"),
        ("idx_parser_category_keyword_category", "parser_category_keyword"),
        ("idx_parser_category_is_enabled", "parser_category"),
        ("idx_parser_category_is_favorite", "parser_category"),
        ("idx_parser_category_is_fallback", "parser_category"),
        ("idx_parser_category_deleted_at", "parser_category"),
        ("idx_parser_category_parent_id", "parser_category"),
        ("idx_parser_supplier_shipping_rate_step_500g", "parser_supplier_shipping_rate"),
        ("idx_parser_supplier_shipping_rate_supplier_id", "parser_supplier_shipping_rate"),
        ("idx_parser_supplier_key", "parser_supplier"),
        ("idx_parser_supplier_category", "parser_supplier"),
    ):
        if _table_exists(bind, table_name):
            try:
                op.drop_index(idx_name, table_name=table_name)
            except Exception:
                pass

    for table_name in (
        "image_asset",
        "parser_weight_keyword",
        "parser_weight_rule",
        "parser_pricing_settings",
        "parser_dedup_decision",
        "parser_favorite_product",
        "parser_category_keyword",
        "parser_category",
        "parser_product",
        "parser_source",
        "parser_supplier_shipping_rate",
        "parser_supplier",
    ):
        if _table_exists(bind, table_name):
            op.drop_table(table_name)

    product_status = postgresql.ENUM(
        "available",
        "out_of_stock",
        "hidden",
        "unavailable",
        name="productstatus",
    )
    product_status.drop(bind, checkfirst=True)
