"""Schemas for categories and settings endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CategoryKeywordRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=255)
    scope: Literal["local", "title"] = "local"


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
    effective_keywords: list[str] = Field(default_factory=list)
    children: list["CategoryTreeNodeResponse"] = Field(default_factory=list)

    class Config:
        from_attributes = True


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
    svc_rules: list[dict] | None = None
    insurance_rules: list[dict] | None = None
    service_fee_rules: list[dict] | None = None
    shipping_rules: dict[str, dict[str, list[dict]]] | None = None


class PricingSupplierRateResponse(BaseModel):
    step_500g: int
    rate_rub: float


class PricingSupplierResponse(BaseModel):
    id: int
    key: str
    name: str
    category: str
    rate_currency: str
    rate_per_500g_value: float
    rate_per_500g_rub: float
    max_step_500g: int
    rates: list[PricingSupplierRateResponse] = Field(default_factory=list)


class PricingSupplierUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=3, max_length=16)
    rate_currency: str | None = Field(default=None, min_length=3, max_length=3)
    rate_per_500g_value: float | None = Field(default=None, ge=0.0, le=1000000.0)
    rate_per_500g_rub: float | None = Field(default=None, ge=0.0, le=1000000.0)
    max_step_500g: int | None = Field(default=None, ge=1, le=1000)


class PricingSupplierCreateRequest(BaseModel):
    key: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    category: str = Field(default="main", min_length=3, max_length=16)
    rate_currency: str = Field(default="RUB", min_length=3, max_length=3)
    rate_per_500g_value: float = Field(default=0.0, ge=0.0, le=1000000.0)
    max_step_500g: int = Field(default=120, ge=1, le=1000)


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
    svc_rules: list[dict] = Field(default_factory=list)
    insurance_rules: list[dict] = Field(default_factory=list)
    service_fee_rules: list[dict] = Field(default_factory=list)
    shipping_rules: dict[str, dict[str, list[dict]]] = Field(default_factory=dict)
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


class ProductResponse(BaseModel):
    id: int
    source_id: int
    handle: str
    title: str
    vendor: str | None = None
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
    internal_category_id: int | None = None
    internal_category_name: str | None = None
    internal_category_slug: str | None = None
    internal_category_ids: list[int] = Field(default_factory=list)
    internal_category_names: list[str] = Field(default_factory=list)
    internal_category_slugs: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


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


CategoryTreeNodeResponse.model_rebuild()
