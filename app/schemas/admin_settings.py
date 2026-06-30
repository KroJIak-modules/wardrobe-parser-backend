"""Schemas for active admin settings endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SettingsTransferImageAssetEntry(BaseModel):
    checksum_sha256: str = Field(min_length=64, max_length=64)
    scope: str = Field(min_length=1, max_length=255)
    file_name: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(ge=0)
    width_px: int | None = Field(default=None, ge=1, le=20000)
    height_px: int | None = Field(default=None, ge=1, le=20000)
    content_base64: str = Field(min_length=1)


class WeightRuleKeywordRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=255)


class WeightRuleCreateRequest(BaseModel):
    weight_grams: int = Field(ge=1, le=100000)


class WeightRuleUpdateRequest(BaseModel):
    weight_grams: int = Field(ge=1, le=100000)


class WeightRuleResponse(BaseModel):
    id: int
    weight_grams: int
    keywords: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class WeightMissingProductResponse(BaseModel):
    id: int
    title: str
    url: str
    source_id: int | None = None
    source_name: str | None = None


class PricingSettingsUpdateRequest(BaseModel):
    markup_multiplier: float | None = Field(default=None, ge=0.1, le=20.0)
    weight_tolerance: float | None = Field(default=None, ge=0.1, le=5.0)
    customs_threshold_eur: float | None = Field(default=None, ge=0.0, le=10000.0)
    customs_duty_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    eur_to_rub_rate: float | None = Field(default=None, ge=0.01, le=1000000.0)
    usd_to_rub_rate: float | None = Field(default=None, ge=0.01, le=1000000.0)
    usdt_to_rub_rate: float | None = Field(default=None, ge=0.01, le=1000000.0)
    usdt_extra_rub: float | None = Field(default=None, ge=0.0, le=1000000.0)
    final_rounding_mode: str | None = Field(default=None, min_length=1, max_length=32)
    payment_fee_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    customs_processing_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    customs_fixed_rub: float | None = Field(default=None, ge=0.0, le=1_000_000.0)
    tax_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class AdminUiSettingsResponse(BaseModel):
    auto_sync_period_minutes: int = Field(ge=60, le=1_000_000, default=60)
    auto_sync_next_run_at: str | None = None
    auto_sync_last_started_at: str | None = None
    auto_sync_last_finished_at: str | None = None
    auto_sync_last_status: str | None = None
    auto_sync_last_error: str | None = None


class AdminUiSettingsUpdateRequest(BaseModel):
    auto_sync_period_minutes: int | None = Field(default=None, ge=60, le=1_000_000)


class PricingSupplierRateResponse(BaseModel):
    min_kg: float
    max_kg: float | None = None
    rub: float


class PricingSupplierResponse(BaseModel):
    id: int
    key: str
    name: str
    provider_kind: str
    parent_supplier_id: int | None = None
    rate_currency: str
    is_enabled: bool
    rates: list[PricingSupplierRateResponse] = Field(default_factory=list)


class PricingSupplierUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    provider_kind: str | None = Field(default=None, min_length=4, max_length=16)
    rate_currency: str | None = Field(default=None, min_length=3, max_length=3)
    is_enabled: bool | None = None
    rates: list[dict] | None = None


class PricingSupplierCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    provider_kind: str = Field(default="main", min_length=4, max_length=16)
    parent_supplier_id: int | None = None
    rate_currency: str = Field(default="RUB", min_length=3, max_length=3)
    is_enabled: bool = True
    rates: list[dict] | None = None


class PricingSettingsResponse(BaseModel):
    markup_multiplier: float
    weight_tolerance: float
    customs_threshold_eur: float
    customs_duty_rate: float
    eur_to_rub_rate: float
    usd_to_rub_rate: float
    usdt_to_rub_rate: float
    usdt_extra_rub: float
    final_rounding_mode: str
    payment_fee_rate: float
    customs_processing_rate: float
    customs_fixed_rub: float
    tax_rate: float
    bybit_rate_status: str = "unknown"
    bybit_rate_warning: str | None = None
    bybit_bucket_step_usdt: int = 0
    bybit_bucket_max_usdt: int = 0
    bybit_bucket_rates: list[dict] = Field(default_factory=list)
    bybit_worker_auto_enabled: bool = True
    bybit_worker_interval_sec: int = 0
    bybit_last_updated_at: str | None = None
    bybit_last_error: str | None = None
    suppliers: list[PricingSupplierResponse] = Field(default_factory=list)
    formula_latex: str = ""
    formula_lines: list[str] = Field(default_factory=list)
    formula_legend: list[dict[str, str]] = Field(default_factory=list)


class SettingsTransferPricingSettings(BaseModel):
    markup_multiplier: float
    weight_tolerance: float
    customs_threshold_eur: float
    customs_duty_rate: float
    eur_to_rub_rate: float
    usd_to_rub_rate: float
    usdt_to_rub_rate: float
    usdt_extra_rub: float
    final_rounding_mode: str
    payment_fee_rate: float
    customs_processing_rate: float
    customs_fixed_rub: float
    tax_rate: float


class SettingsTransferAdminUiSettings(BaseModel):
    auto_sync_period_minutes: int = Field(ge=60, le=1_000_000, default=60)


class SettingsTransferSupplierRateEntry(BaseModel):
    min_kg: float = Field(ge=0.0, le=100000.0)
    max_kg: float | None = Field(default=None, ge=0.0, le=100000.0)
    rub: float = Field(ge=0.0, le=100000000.0)


class SettingsTransferSupplierEntry(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    provider_kind: str = Field(default="main", min_length=4, max_length=16)
    parent_supplier_key: str | None = Field(default=None, min_length=1, max_length=64)
    rate_currency: str = Field(default="RUB", min_length=3, max_length=3)
    is_enabled: bool = True
    rates: list[SettingsTransferSupplierRateEntry] = Field(default_factory=list)


class SettingsTransferSourceEntry(BaseModel):
    key: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1, max_length=2048)
    adapter_key: str | None = Field(default=None, min_length=1, max_length=255)
    parser_config: dict = Field(default_factory=dict)
    sort_priority: int = Field(ge=1, le=1000000)
    enabled: bool = True
    sync_enabled: bool = True
    dedup_enabled: bool = True
    hide_auto_added_products: bool = False
    description_mode: Literal["hidden", "text", "html"] = "text"
    show_images: bool = True
    supplier_key: str | None = Field(default=None, min_length=1, max_length=64)
    promo_factor: float = Field(default=1.0, ge=0.0, le=10.0)
    promo_only_no_discount: bool = False
    buyout_surcharge_value: float | None = Field(default=None, ge=0.0, le=100000000.0)
    buyout_surcharge_currency: str | None = Field(default=None, min_length=3, max_length=3)
    logo_asset_checksum: str | None = Field(default=None, min_length=64, max_length=64)


class SettingsTransferWeightRuleEntry(BaseModel):
    weight_grams: int = Field(ge=1, le=1000000)
    keywords: list[str] = Field(default_factory=list)


class SettingsTransferDesignerSourceNameEntry(BaseModel):
    source_name: str = Field(min_length=1, max_length=255)
    designer_name: str | None = Field(default=None, min_length=1, max_length=255)
    is_enabled: bool = True
    is_admin_touched: bool = False


class SettingsTransferDesignerEntry(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)
    description: str | None = None
    origin_kind: Literal["auto", "manual"] = "manual"
    is_admin_touched: bool = False
    is_enabled: bool = True


class SettingsTransferTaxonomyFilterNode(BaseModel):
    slug: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1)
    display_title: str | None = None
    mobile_pair_slug: str | None = Field(default=None, min_length=1, max_length=255)
    node_kind: Literal["filter", "multifilter"] = "filter"
    is_enabled: bool = True
    local_category_keywords: list[str] = Field(default_factory=list)
    title_keywords: list[str] = Field(default_factory=list)
    children: list["SettingsTransferTaxonomyFilterNode"] = Field(default_factory=list)


class SettingsTransferTaxonomyCustomCatalog(BaseModel):
    slug: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1)
    description: str | None = None
    is_enabled: bool = True


class SettingsTransferTaxonomyShowcaseAttachment(BaseModel):
    kind: Literal["filter", "custom_catalog"]
    filter_slug: str | None = Field(default=None, min_length=1, max_length=255)
    custom_catalog_slug: str | None = Field(default=None, min_length=1, max_length=255)
    hidden_filter_slugs: list[str] = Field(default_factory=list)


class SettingsTransferTaxonomyShowcaseCategory(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1)
    attachments: list[SettingsTransferTaxonomyShowcaseAttachment] = Field(default_factory=list)


class SettingsTransferTaxonomyState(BaseModel):
    filters: list[SettingsTransferTaxonomyFilterNode] = Field(default_factory=list)
    custom_catalogs: list[SettingsTransferTaxonomyCustomCatalog] = Field(default_factory=list)
    showcase_categories: list[SettingsTransferTaxonomyShowcaseCategory] = Field(default_factory=list)


class SettingsTransferShowcaseCarouselEntry(BaseModel):
    asset_checksum: str = Field(min_length=64, max_length=64)
    viewport: Literal["desktop", "mobile"]
    position: int = Field(ge=1, le=1000)


class SettingsTransferShowcaseMedia(BaseModel):
    desktop_hero_asset_checksum: str | None = Field(default=None, min_length=64, max_length=64)
    mobile_hero_asset_checksum: str | None = Field(default=None, min_length=64, max_length=64)
    desktop_carousel: list[SettingsTransferShowcaseCarouselEntry] = Field(default_factory=list)
    mobile_carousel: list[SettingsTransferShowcaseCarouselEntry] = Field(default_factory=list)


class SettingsTransferPayload(BaseModel):
    schema_version: int = Field(default=5, ge=1, le=1000)
    exported_at: str | None = None
    project: str | None = None
    pricing_settings: SettingsTransferPricingSettings
    admin_ui_settings: SettingsTransferAdminUiSettings
    suppliers: list[SettingsTransferSupplierEntry] = Field(default_factory=list)
    sources: list[SettingsTransferSourceEntry] = Field(default_factory=list)
    weight_rules: list[SettingsTransferWeightRuleEntry] = Field(default_factory=list)
    designers: list[SettingsTransferDesignerEntry] = Field(default_factory=list)
    designer_source_names: list[SettingsTransferDesignerSourceNameEntry] = Field(default_factory=list)
    taxonomy: SettingsTransferTaxonomyState = Field(default_factory=SettingsTransferTaxonomyState)
    showcase_media: SettingsTransferShowcaseMedia = Field(default_factory=SettingsTransferShowcaseMedia)
    image_assets: list[SettingsTransferImageAssetEntry] = Field(default_factory=list)


class SettingsTransferResponse(BaseModel):
    ok: bool
    message: str
    schema_version: int
    imported_at: str
    imported_counts: dict[str, int] = Field(default_factory=dict)


SettingsTransferTaxonomyFilterNode.model_rebuild()
