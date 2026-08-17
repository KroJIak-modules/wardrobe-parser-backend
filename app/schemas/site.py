from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SiteRouteTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pathname: str
    query: dict[str, str | list[str]] | None = None


class SiteMediaAssetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    url: str
    media_kind: Literal["image", "video"]
    mime_type: str
    byte_size: int
    width_px: int | None = None
    height_px: int | None = None


class SiteHeroResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    viewport: Literal["desktop", "mobile"]
    asset: SiteMediaAssetResponse | None = None


class SiteCarouselItemResponse(SiteMediaAssetResponse):
    position: int


class SiteCarouselResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    viewport: Literal["desktop", "mobile"]
    items: list[SiteCarouselItemResponse] = Field(default_factory=list)


class SiteHomeNotificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    enabled: bool
    delay_ms: int
    title: str
    description: str
    image_src: str
    cta_label: str
    cta_href: str


class SiteAccessStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    unlocked: bool
    title: str
    description: str


class SiteAccessUnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = ""


class SiteAccessUnlockResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    unlocked: bool


class SiteNavigationMenuEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    presentation: Literal["heading", "item"]
    description: str | None = None
    target: SiteRouteTarget | None = None


class SiteNavigationMenuColumnTitle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    target: SiteRouteTarget | None = None


class SiteNavigationMenuColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    align: Literal["start", "center"]
    title: SiteNavigationMenuColumnTitle | None = None
    entries: list[SiteNavigationMenuEntry] = Field(default_factory=list)


class SiteNavigationMenu(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Literal["new", "designers", "men", "women"]
    columns: list[SiteNavigationMenuColumn] = Field(default_factory=list)
    footer_link: SiteNavigationMenuEntry | None = None


class SiteNavigationTopSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Literal["new", "designers", "men", "women", "sale"]
    label: str
    target: SiteRouteTarget | None = None


class SiteMobileMenuRootGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    # Entries retain the exact labels, targets, ordering and gender scope from
    # the same admin-configured desktop menu columns.
    entries: list[SiteNavigationMenuEntry] = Field(default_factory=list)


class SiteNavigationMobileMenu(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Mobile navigation is derived from the same gender-scoped showcase
    # attachments as the desktop men/women menus.
    groups_by_gender: dict[str, list[SiteMobileMenuRootGroup]] = Field(default_factory=dict)


class SiteNavigationCatalogContextEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    label: str
    description: str | None = None


class SiteNavigationCatalogContexts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    designers: list[SiteNavigationCatalogContextEntry] = Field(default_factory=list)
    custom_catalogs: list[SiteNavigationCatalogContextEntry] = Field(default_factory=list)


class SiteNavigationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_sections: list[SiteNavigationTopSection] = Field(default_factory=list)
    desktop_menus: dict[str, SiteNavigationMenu] = Field(default_factory=dict)
    mobile_menu: SiteNavigationMobileMenu
    catalog_contexts: SiteNavigationCatalogContexts


class SiteCatalogFilterOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    value: str


class SiteCatalogFilterGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    query_param: str
    selection_mode: Literal["single", "multiple"]
    options: list[SiteCatalogFilterOption] = Field(default_factory=list)
    panel_width: Literal["compact", "wide"] | None = None
    max_visible_options: int | None = None
    prioritize_selected: bool | None = None


class SiteCatalogHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str | None = None
    source: Literal["search", "sale", "custom_catalog", "designer", "menu_filter", "all_products", "multiple_designers", "catalog"]


class SiteCatalogExperienceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    header: SiteCatalogHeader
    filter_groups: list[SiteCatalogFilterGroup] = Field(default_factory=list)


class SiteCatalogProductBrand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    slug: str | None = None


class SiteCatalogProductResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    path: str
    brand: SiteCatalogProductBrand
    name: str
    price_rub: int | None = None
    old_price_rub: int | None = None
    status: Literal["in_stock", "preorder", "sold_out"]
    image_url: str | None = None


class SiteCatalogProductsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SiteCatalogProductResponse] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class SiteDesignerEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    label: str
    letter: str


class SiteDesignersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alphabet: list[str] = Field(default_factory=list)
    entries: list[SiteDesignerEntryResponse] = Field(default_factory=list)


class SiteProductDescriptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["text"]
    content: str


class SiteProductSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    url: str | None = None
    logo_url: str | None = None


class SiteProductVariantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    size: str
    price_rub: int | None = None
    old_price_rub: int | None = None
    source: SiteProductSourceResponse


class SiteProductRecommendationContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    designer_slug: str | None = None
    section_slug: str | None = None
    gender: Literal["men", "women"] | None = None


class SiteProductResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    path: str
    handle: str
    brand: SiteCatalogProductBrand
    name: str
    description: SiteProductDescriptionResponse | None = None
    status: Literal["in_stock", "preorder", "sold_out"]
    photos: list[str] = Field(default_factory=list)
    variants: list[SiteProductVariantResponse] = Field(default_factory=list)
    primary_source_url: str | None = None
    recommendation_context: SiteProductRecommendationContextResponse


class SiteAboutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    photos: list[SiteMediaAssetResponse] = Field(default_factory=list)


class SiteQuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    question: str
    answer: str
    is_expanded_by_default: bool


class SiteQuestionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SiteQuestionResponse] = Field(default_factory=list)


class SiteCartQuoteItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(gt=0)
    variant_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=100)


class SiteCartQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SiteCartQuoteItemRequest] = Field(default_factory=list, max_length=100)


class SiteCartQuoteItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: int
    quantity: int
    availability: Literal["in_stock", "preorder"]
    original_line_total_rub: float
    old_line_total_rub: float | None = None
    final_line_total_rub: float


class SiteCartQuoteSvcTierResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_rub: float
    max_rub: float | None = None
    mode: Literal["fixed_rub", "percent"]
    value: float
    amount_rub: float | None = None
    is_applied: bool = False


class SiteCartQuoteSvcProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preorder_subtotal_rub: float
    applied_amount_rub: float
    next_threshold_rub: float | None = None
    amount_to_next_threshold_rub: float | None = None


class SiteCartQuoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SiteCartQuoteItemResponse] = Field(default_factory=list)
    unavailable_variant_ids: list[int] = Field(default_factory=list)
    # The price before cart-wide SVC/source-surcharge consolidation. It deliberately
    # excludes each product's compare-at price, which belongs to the line item only.
    original_total_rub: float
    final_total_rub: float
    total_rub: float
    svc_tiers: list[SiteCartQuoteSvcTierResponse] = Field(default_factory=list)
    svc_progress: SiteCartQuoteSvcProgressResponse
