"""Schemas for categories and settings endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CategoryKeywordRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=255)
    scope: Literal["local", "title", "status"] = "local"


class CategoryManualProductRequest(BaseModel):
    product_id: int


class CategoryManualProductResponse(BaseModel):
    product_id: int
    source_id: int
    source_name: str | None = None
    title: str
    url: str
    status: str
    image_url: str | None = None
    category_names: list[str] = Field(default_factory=list)


class CategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = None


class CategoryUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: int | None = None
    is_enabled: bool | None = None
    is_favorite: bool | None = None


class CategoryTreeNodeResponse(BaseModel):
    id: int
    name: str
    slug: str
    parent_id: int | None = None
    is_fallback: bool
    is_favorite: bool = False
    is_enabled: bool = True
    is_system: bool = False
    has_children: bool = False
    keywords_editable: bool = True
    keywords_locked_reason: str | None = None
    is_designers_root: bool = False
    is_in_designers_branch: bool = False
    product_count: int = 0
    keywords: list[str] = Field(default_factory=list)
    title_keywords: list[str] = Field(default_factory=list)
    status_keywords: list[str] = Field(default_factory=list)
    effective_keywords: list[str] = Field(default_factory=list)
    children: list["CategoryTreeNodeResponse"] = Field(default_factory=list)

    class Config:
        from_attributes = True


class CatalogCategoryNodeResponse(BaseModel):
    slug: str
    name: str
    count: int = 0
    is_designers_root: bool = False
    is_in_designers_branch: bool = False
    children: list["CatalogCategoryNodeResponse"] = Field(default_factory=list)


class CatalogProductCardResponse(BaseModel):
    id: int
    source_id: int
    title: str
    vendor: str | None = None
    vendor_original: str | None = None
    vendor_mapped: str | None = None
    vendor_display: str | None = None
    url: str
    price: float | None = None
    currency: str
    source_price: float | None = None
    source_currency: str | None = None
    status: str
    image_count: int = 0
    image_urls: list[str] = Field(default_factory=list)
    image_ids: list[int] = Field(default_factory=list)
    buyout_price_rub: float | None = None
    is_favorite: bool = False


class CatalogProductsResponse(BaseModel):
    items: list[CatalogProductCardResponse] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    limit: int


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

    class Config:
        from_attributes = True


class WeightMissingProductResponse(BaseModel):
    id: int
    title: str
    url: str
    source_id: int
    source_name: str


class PricingSettingsUpdateRequest(BaseModel):
    markup_multiplier: float | None = Field(default=None, ge=0.1, le=20.0)
    weight_tolerance: float | None = Field(default=None, ge=0.1, le=5.0)
    promo_factor: float | None = Field(default=None, ge=0.1, le=5.0)
    customs_threshold_eur: float | None = Field(default=None, ge=0.0, le=10000.0)
    customs_threshold_currency: str | None = Field(default=None, min_length=3, max_length=3)
    customs_duty_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    bybit_extra_rub: float | None = Field(default=None, ge=0.0, le=1000.0)
    eur_to_usd_rate: float | None = Field(default=None, ge=0.01, le=1000.0)
    gbp_to_usd_rate: float | None = Field(default=None, ge=0.01, le=1000.0)
    final_rounding_mode: str | None = Field(default=None, min_length=1, max_length=32)
    payment_fee_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    customs_processing_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    customs_fixed_rub: float | None = Field(default=None, ge=0.0, le=1_000_000.0)
    shipping_alt_threshold_eur: float | None = Field(default=None, ge=0.0, le=100_000.0)
    tax_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    designers_min_products: int | None = Field(default=None, ge=1, le=1_000_000)
    designers_exclude_store_vendors: bool | None = None
    dedup_only_available_products: bool | None = None
    svc_rules: list[dict] | None = None
    insurance_rules: list[dict] | None = None
    service_fee_rules: list[dict] | None = None
    shipping_rules: dict[str, dict[str, list[dict]]] | None = None
    showcase_hero_image_asset_id: int | None = None
    showcase_carousel_image_asset_ids: list[int] | None = None


class ShowcaseMediaSettingsUpdateRequest(BaseModel):
    showcase_hero_image_asset_id: int | None = None
    showcase_carousel_image_asset_ids: list[int] | None = None


class ShowcaseMediaSettingsResponse(BaseModel):
    showcase_hero_image_asset_id: int | None = None
    showcase_carousel_image_asset_ids: list[int] = Field(default_factory=list)
    carousel_limit: int = 20


class PricingSupplierRateResponse(BaseModel):
    min_kg: float
    max_kg: float | None = None
    rub: float


class PricingSupplierResponse(BaseModel):
    id: int
    key: str
    name: str
    category: str
    parent_supplier_id: int | None = None
    alt_position: int = 0
    rate_currency: str
    rates: list[PricingSupplierRateResponse] = Field(default_factory=list)


class PricingSupplierUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=3, max_length=16)
    alt_position: int | None = Field(default=None, ge=0, le=3)
    rate_currency: str | None = Field(default=None, min_length=3, max_length=3)
    rates: list[dict] | None = None


class PricingSupplierCreateRequest(BaseModel):
    key: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    category: str = Field(default="main", min_length=3, max_length=16)
    parent_supplier_id: int | None = None
    alt_position: int | None = Field(default=None, ge=0, le=3)
    rate_currency: str = Field(default="RUB", min_length=3, max_length=3)
    rates: list[dict] | None = None


class PricingSettingsResponse(BaseModel):
    markup_multiplier: float
    weight_tolerance: float
    promo_factor: float
    customs_threshold_eur: float
    customs_threshold_currency: str
    customs_duty_rate: float
    bybit_usdt_to_rub: float
    bybit_extra_rub: float
    eur_to_usd_rate: float
    gbp_to_usd_rate: float
    final_rounding_mode: str
    payment_fee_rate: float
    customs_processing_rate: float
    customs_fixed_rub: float
    shipping_alt_threshold_eur: float
    tax_rate: float
    designers_min_products: int
    designers_exclude_store_vendors: bool
    dedup_only_available_products: bool
    svc_rules: list[dict] = Field(default_factory=list)
    insurance_rules: list[dict] = Field(default_factory=list)
    service_fee_rules: list[dict] = Field(default_factory=list)
    shipping_rules: dict[str, dict[str, list[dict]]] = Field(default_factory=dict)
    showcase_hero_image_asset_id: int | None = None
    showcase_carousel_image_asset_ids: list[int] = Field(default_factory=list)
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
    promo_factor: float
    customs_threshold_eur: float
    customs_threshold_currency: str
    customs_duty_rate: float
    bybit_extra_rub: float
    eur_to_usd_rate: float
    gbp_to_usd_rate: float
    final_rounding_mode: str
    payment_fee_rate: float
    customs_processing_rate: float
    customs_fixed_rub: float
    shipping_alt_threshold_eur: float
    tax_rate: float
    designers_min_products: int = Field(ge=1, le=1_000_000)
    designers_exclude_store_vendors: bool = False
    dedup_only_available_products: bool = False
    svc_rules: list[dict] = Field(default_factory=list)
    insurance_rules: list[dict] = Field(default_factory=list)
    service_fee_rules: list[dict] = Field(default_factory=list)
    shipping_rules: dict[str, dict[str, list[dict]]] = Field(default_factory=dict)
    showcase_hero_image_asset_id: int | None = None
    showcase_carousel_image_asset_ids: list[int] = Field(default_factory=list)


class SettingsTransferSupplierRateEntry(BaseModel):
    min_kg: float = Field(ge=0.0, le=100000.0)
    max_kg: float | None = Field(default=None, ge=0.0, le=100000.0)
    rub: float = Field(ge=0.0, le=100000000.0)


class SettingsTransferSupplierEntry(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    category: str = Field(default="main", min_length=3, max_length=16)
    parent_supplier_key: str | None = Field(default=None, min_length=1, max_length=64)
    alt_position: int = Field(default=0, ge=0, le=3)
    rate_currency: str = Field(default="RUB", min_length=3, max_length=3)
    rates: list[SettingsTransferSupplierRateEntry] = Field(default_factory=list)


class SettingsTransferSourceEntry(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1, max_length=2048)
    enabled: bool = True
    supplier_key: str | None = Field(default=None, min_length=1, max_length=64)
    promo_factor: float = Field(default=1.0, ge=0.0, le=10.0)
    promo_only_no_discount: bool = False
    buyout_surcharge_value: float = Field(default=0.0, ge=0.0, le=100000000.0)
    buyout_surcharge_currency: str = Field(default="RUB", min_length=3, max_length=3)


class SettingsTransferWeightRuleEntry(BaseModel):
    weight_grams: int = Field(ge=1, le=1000000)
    sort_order: int = Field(default=0, ge=0, le=1000000)
    keywords: list[str] = Field(default_factory=list)


class SettingsTransferCategoryEntry(BaseModel):
    slug: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    parent_slug: str | None = Field(default=None, min_length=1, max_length=255)
    is_fallback: bool = False
    is_favorite: bool = False
    is_enabled: bool = True


class SettingsTransferCategoryKeywordEntry(BaseModel):
    category_slug: str = Field(min_length=1, max_length=255)
    keyword: str = Field(min_length=1, max_length=255)
    scope: Literal["local", "title"] = "local"


class SettingsTransferPayload(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=1000)
    exported_at: str | None = None
    project: str | None = None
    pricing_settings: SettingsTransferPricingSettings
    suppliers: list[SettingsTransferSupplierEntry] = Field(default_factory=list)
    sources: list[SettingsTransferSourceEntry] = Field(default_factory=list)
    weight_rules: list[SettingsTransferWeightRuleEntry] = Field(default_factory=list)
    categories: list[SettingsTransferCategoryEntry] = Field(default_factory=list)
    category_keywords: list[SettingsTransferCategoryKeywordEntry] = Field(default_factory=list)


class SettingsTransferResponse(BaseModel):
    ok: bool
    message: str
    schema_version: int
    imported_at: str
    imported_counts: dict[str, int] = Field(default_factory=dict)


class ProductResponse(BaseModel):
    id: int
    source_id: int
    handle: str
    title: str
    vendor: str | None = None
    vendor_original: str | None = None
    vendor_mapped: str | None = None
    vendor_display: str | None = None
    product_type: str | None = None
    url: str
    price: float | None = None
    currency: str
    status: str = "available"
    image_count: int = 0
    image_urls: list[str] = Field(default_factory=list)
    image_ids: list[int] = Field(default_factory=list)
    weight_grams: float | None = None
    weight_source: str | None = None
    weight_match_keyword: str | None = None
    weight_value: float | None = None
    weight_unit: str | None = None
    variants: list[dict] = Field(default_factory=list)
    is_favorite: bool = False
    starred_category_ids: list[int] = Field(default_factory=list)
    internal_category_id: int | None = None
    internal_category_name: str | None = None
    internal_category_slug: str | None = None
    internal_category_ids: list[int] = Field(default_factory=list)
    internal_category_names: list[str] = Field(default_factory=list)
    internal_category_slugs: list[str] = Field(default_factory=list)
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class ShowcaseProductResponse(BaseModel):
    id: int
    source_id: int
    title: str
    vendor: str | None = None
    vendor_original: str | None = None
    vendor_mapped: str | None = None
    vendor_display: str | None = None
    url: str
    price: float | None = None
    currency: str
    source_price: float | None = None
    source_currency: str | None = None
    final_price: float | None = None
    final_currency: str | None = None
    status: str = "available"
    image_urls: list[str] = Field(default_factory=list)
    image_ids: list[int] = Field(default_factory=list)
    variants: list[dict] = Field(default_factory=list)
    internal_category_name: str | None = None
    internal_category_names: list[str] = Field(default_factory=list)
    description: str | None = None
    pricing_components: dict[str, object] = Field(default_factory=dict)
    product_edit: dict[str, object] = Field(default_factory=dict)


class PricingExampleProductResponse(BaseModel):
    product_id: int
    title: str
    url: str
    source_name: str | None = None
    image_url: str | None = None
    source_price: float | None = None
    source_currency: str | None = None
    final_price: float | None = None
    components: dict[str, object] = Field(default_factory=dict)


class BrandMappingItemResponse(BaseModel):
    source_brand: str
    target_brand: str
    include_in_designers: bool = True


class BrandMappingListResponse(BaseModel):
    items: list[BrandMappingItemResponse] = Field(default_factory=list)
    known_targets: list[str] = Field(default_factory=list)


class BrandMappingUpdateRequest(BaseModel):
    items: list[BrandMappingItemResponse] = Field(default_factory=list)


class DedupCandidateResponse(BaseModel):
    pair_key: str
    score: float
    reasons: list[str] = Field(default_factory=list)
    left: ProductResponse
    right: ProductResponse


class DedupCandidateListResponse(BaseModel):
    items: list[DedupCandidateResponse]
    total: int
    limit: int


class DedupMergeRequest(BaseModel):
    primary_product_id: int
    duplicate_product_id: int


class DedupRejectRequest(BaseModel):
    product_a_id: int
    product_b_id: int


class DedupCombineRequest(BaseModel):
    product_a_id: int
    product_b_id: int


class DedupDecisionResponse(BaseModel):
    pair_key: str
    action: str
    decided_at: datetime | None = None
    can_undo: bool = False
    undo_block_reason: str | None = None
    left: ProductResponse
    right: ProductResponse


class DedupDecisionListResponse(BaseModel):
    items: list[DedupDecisionResponse]
    total: int
    limit: int


class DedupUndoRequest(BaseModel):
    pair_key: str


CategoryTreeNodeResponse.model_rebuild()
CatalogCategoryNodeResponse.model_rebuild()
