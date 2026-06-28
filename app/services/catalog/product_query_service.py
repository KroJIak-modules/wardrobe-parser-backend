from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import String, and_, case, cast, func, literal, or_
from sqlalchemy.orm import Session, joinedload

from app.models import (
    CustomCatalog,
    CustomCatalogProduct,
    Designer,
    Filter,
    FilterAssignmentRuntimeState,
    FilterNode,
    Product,
    ProductFilterAssignment,
    ProductListing,
    ProductListingMember,
    ProductPresentation,
    Source,
    ShowcaseCategory,
    ShowcaseCategoryAttachment,
)
from app.repositories.catalog_products import CatalogProductRepository
from app.schemas.admin_settings import PricingSettingsResponse, PricingSupplierRateResponse, PricingSupplierResponse
from app.services.catalog.product_title_service import ProductTitleService
from app.services.catalog.source_registry_service import SourceRegistryService
from app.services.settings.pricing_service import PricingSettingsService

UNMATCHED_FILTER_SLUG = "__none__"
UNMATCHED_FILTER_LABEL = "Без фильтров"


class ProductQueryService:
    _PRICING_EXAMPLE_SCAN_LIMIT = 500
    _PRICING_EXAMPLE_BATCH_SIZE = 25

    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = CatalogProductRepository(db)
        self.pricing = PricingSettingsService(db)
        self._filter_rows_cache: list[tuple[str, str, bool]] | None = None
        self._custom_catalog_rows_cache: list[tuple[str, str]] | None = None
        self._showcase_cache: list[ShowcaseCategory] | None = None
        self._source_rows_cache: list[tuple[int, str, str, str]] | None = None
        self._source_mode_ids_cache: dict[str, list[int]] = {}
        self._assigned_filter_slug_by_product_id_cache: dict[int, str] | None = None
        self._assigned_filter_label_by_product_id_cache: dict[int, str] | None = None
        self._applied_filter_revision_cache: int | None = None
        self._pricing_settings_cache: PricingSettingsResponse | None = None
        self._variant_pricing_cache: dict[tuple[int, float | None, str | None, float | None, int | None], tuple[float | None, dict | None]] = {}

    def _filter_rows(self) -> list[tuple[str, str, bool]]:
        if self._filter_rows_cache is None:
            rows = (
                self.db.query(
                    Filter.slug.label("slug"),
                    func.trim(func.coalesce(Filter.display_title, Filter.title)).label("label"),
                    Filter.is_enabled.label("is_enabled"),
                )
                .order_by(Filter.slug.asc(), Filter.id.asc())
                .all()
            )
            self._filter_rows_cache = [
                (str(slug), str(label or "").strip(), bool(is_enabled))
                for slug, label, is_enabled in rows
                if str(slug or "").strip() and str(label or "").strip()
            ]
        return self._filter_rows_cache

    def _custom_catalog_rows(self) -> list[tuple[str, str]]:
        if self._custom_catalog_rows_cache is None:
            rows = (
                self.db.query(
                    CustomCatalog.slug.label("slug"),
                    func.trim(CustomCatalog.title).label("title"),
                )
                .order_by(CustomCatalog.slug.asc(), CustomCatalog.id.asc())
                .all()
            )
            self._custom_catalog_rows_cache = [
                (str(slug), str(title or "").strip())
                for slug, title in rows
                if str(slug or "").strip() and str(title or "").strip()
            ]
        return self._custom_catalog_rows_cache

    def _source_rows(self) -> list[tuple[int, str, str, str]]:
        if self._source_rows_cache is None:
            rows = (
                self.db.query(
                    Source.id.label("id"),
                    Source.key.label("key"),
                    Source.name.label("name"),
                    Source.parser_config.label("parser_config"),
                )
                .order_by(Source.name.asc(), Source.id.asc())
                .all()
            )
            self._source_rows_cache = [
                (
                    int(source_id),
                    str(source_key or "").strip(),
                    str(source_name or "").strip() or str(source_key or "").strip(),
                    SourceRegistryService.normalize_parser_mode((parser_config or {}).get("mode") if isinstance(parser_config, dict) else None),
                )
                for source_id, source_key, source_name, parser_config in rows
                if source_id is not None and str(source_key or "").strip()
            ]
        return self._source_rows_cache

    def _pricing_settings(self) -> PricingSettingsResponse:
        if self._pricing_settings_cache is None:
            self._pricing_settings_cache = self.pricing.get_settings(refresh_bybit=False)
        return self._pricing_settings_cache

    def _filtered_product_ids_subquery(
        self,
        *,
        query: str = "",
        source_id: int | None = None,
        source_mode: str | None = None,
        designer_filter: str | None = None,
        gender: str | None = None,
        filter_slug: str | None = None,
        custom_catalog_slug: str | None = None,
        visibility_status: str | None = None,
        availability_mode: str | None = None,
        orderability_status: str | None = None,
        audience: str = "admin",
        alias: str = "filtered_products",
    ):
        return (
            self._base_product_id_query(
                query=query,
                source_id=source_id,
                source_mode=source_mode,
                designer_filter=designer_filter,
                gender=gender,
                filter_slug=filter_slug,
                custom_catalog_slug=custom_catalog_slug,
                visibility_status=visibility_status,
                availability_mode=availability_mode,
                orderability_status=orderability_status,
                audience=audience,
            )
            .distinct()
            .subquery(alias)
        )

    def _showcase_categories(self) -> list[ShowcaseCategory]:
        if self._showcase_cache is None:
            self._showcase_cache = (
                self.db.query(ShowcaseCategory)
                .options(
                    joinedload(ShowcaseCategory.attachments).joinedload(ShowcaseCategoryAttachment.filter),
                    joinedload(ShowcaseCategory.attachments).joinedload(ShowcaseCategoryAttachment.custom_catalog),
                )
                .order_by(ShowcaseCategory.code.asc(), ShowcaseCategory.id.asc())
                .all()
            )
        return self._showcase_cache

    def _assigned_filter_slug_by_product_id(self) -> dict[int, str]:
        if self._assigned_filter_slug_by_product_id_cache is None:
            self._load_assignment_caches()
        return dict(self._assigned_filter_slug_by_product_id_cache)

    def _assigned_filter_label_by_product_id(self) -> dict[int, str]:
        if self._assigned_filter_label_by_product_id_cache is None:
            self._load_assignment_caches()
        return dict(self._assigned_filter_label_by_product_id_cache)

    def _applied_filter_revision(self) -> int:
        if self._applied_filter_revision_cache is None:
            state = (
                self.db.query(FilterAssignmentRuntimeState)
                .filter(FilterAssignmentRuntimeState.id == 1)
                .one_or_none()
            )
            self._applied_filter_revision_cache = int(getattr(state, "applied_revision", 0) or 0)
        return int(self._applied_filter_revision_cache or 0)

    def _load_assignment_caches(self) -> None:
        revision = self._applied_filter_revision()
        slug_by_product_id: dict[int, str] = {}
        label_by_product_id: dict[int, str] = {}
        if revision > 0:
            rows = (
                self.db.query(
                    ProductFilterAssignment.product_id,
                    ProductFilterAssignment.filter_slug,
                    ProductFilterAssignment.filter_label,
                )
                .filter(ProductFilterAssignment.revision == revision)
                .all()
            )
            for product_id, slug, label in rows:
                normalized_slug = str(slug or "").strip()
                if product_id is None or not normalized_slug:
                    continue
                slug_by_product_id[int(product_id)] = normalized_slug
                label_by_product_id[int(product_id)] = str(label or "").strip() or normalized_slug
        self._assigned_filter_slug_by_product_id_cache = slug_by_product_id
        self._assigned_filter_label_by_product_id_cache = label_by_product_id

    @staticmethod
    def _normalized_text_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for raw_item in value:
            item = str(raw_item or "").strip()
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    def _matched_filter_slugs(self, product: Product) -> list[str]:
        slug = self._assigned_filter_slug_by_product_id().get(int(product.id))
        return [slug] if slug else []

    def _matched_filter_labels(self, product: Product) -> list[str]:
        label = self._assigned_filter_label_by_product_id().get(int(product.id))
        return [label] if label else []

    def _assigned_filter_subquery(self, product_ids_subquery, *, alias: str):
        revision = self._applied_filter_revision()
        return (
            self.db.query(
                ProductFilterAssignment.product_id.label("product_id"),
                ProductFilterAssignment.filter_slug.label("slug"),
                ProductFilterAssignment.filter_label.label("label"),
            )
            .select_from(product_ids_subquery)
            .join(
                ProductFilterAssignment,
                and_(
                    ProductFilterAssignment.product_id == product_ids_subquery.c.product_id,
                    ProductFilterAssignment.revision == revision,
                ),
            )
            .subquery(alias)
        )

    def _custom_catalog_slugs(self, product_id: int) -> list[str]:
        rows = (
            self.db.query(CustomCatalog.slug)
            .join(CustomCatalogProduct, CustomCatalogProduct.catalog_id == CustomCatalog.id)
            .filter(CustomCatalogProduct.product_id == int(product_id))
            .order_by(CustomCatalog.slug.asc(), CustomCatalog.id.asc())
            .all()
        )
        return [str(row.slug) for row in rows if str(row.slug or "").strip()]

    def _taxonomy_titles(
        self,
        *,
        filter_slugs: list[str],
        custom_catalog_slugs: list[str],
    ) -> list[str]:
        filter_title_by_slug = {
            slug: label
            for slug, label, _ in self._filter_rows()
        }
        custom_catalog_title_by_slug = {
            slug: title
            for slug, title in self._custom_catalog_rows()
        }
        titles: list[str] = []
        seen: set[str] = set()
        for slug in filter_slugs:
            title = str(filter_title_by_slug.get(str(slug), "")).strip()
            if title and title not in seen:
                seen.add(title)
                titles.append(title)
        for slug in custom_catalog_slugs:
            title = str(custom_catalog_title_by_slug.get(str(slug), "")).strip()
            if title and title not in seen:
                seen.add(title)
                titles.append(title)
        return titles

    def _showcase_category_codes_for_product(self, *, matched_filter_slugs: list[str], custom_catalog_slugs: list[str]) -> list[str]:
        filter_slug_set = set(matched_filter_slugs)
        custom_catalog_slug_set = set(custom_catalog_slugs)
        result: list[str] = []
        for category in self._showcase_categories():
            for attachment in sorted(category.attachments, key=lambda item: (int(item.position), int(item.id))):
                if attachment.attachment_kind == "filter" and attachment.filter is not None and str(attachment.filter.slug) in filter_slug_set:
                    result.append(str(category.code))
                    break
                if attachment.attachment_kind == "custom_catalog" and attachment.custom_catalog is not None and str(attachment.custom_catalog.slug) in custom_catalog_slug_set:
                    result.append(str(category.code))
                    break
        return result

    def _apply_filter_slug_query(self, base_query, slug: str):
        normalized_slug = str(slug or "").strip()
        product_scope = base_query.with_entities(Product.id.label("product_id")).distinct().subquery("filter_scope_products")
        assigned_filters = self._assigned_filter_subquery(product_scope, alias="filter_scope_assigned_filters")
        if normalized_slug == UNMATCHED_FILTER_SLUG:
            return (
                base_query.outerjoin(assigned_filters, assigned_filters.c.product_id == Product.id)
                .filter(assigned_filters.c.product_id.is_(None))
            )
        return (
            base_query.join(assigned_filters, assigned_filters.c.product_id == Product.id)
            .filter(assigned_filters.c.slug == normalized_slug)
        )

    def _designer_option_rows(self, context_subquery) -> list[tuple[str, str, int]]:
        designer_listing = ProductListing.__table__.alias("designer_listing")
        designer_presentation = ProductPresentation.__table__.alias("designer_presentation")
        normalized_designer_name = func.lower(
            func.trim(func.coalesce(designer_presentation.c.brand_override_name, designer_listing.c.source_designer_raw, ""))
        )
        designer_key_expr = case(
            (Product.designer_id.is_not(None), cast(Product.designer_id, String())),
            else_=func.concat(literal("name:"), normalized_designer_name),
        )
        designer_label_expr = case(
            (
                and_(Product.designer_id.is_not(None), func.length(func.trim(func.coalesce(Designer.name, ""))) > 0),
                func.trim(Designer.name),
            ),
            else_=func.trim(func.coalesce(designer_presentation.c.brand_override_name, designer_listing.c.source_designer_raw, "")),
        )
        rows = (
            self.db.query(
                designer_key_expr.label("value"),
                designer_label_expr.label("label"),
                func.count().label("count"),
            )
            .select_from(context_subquery)
            .join(Product, Product.id == context_subquery.c.product_id)
            .outerjoin(Designer, Designer.id == Product.designer_id)
            .outerjoin(designer_presentation, designer_presentation.c.product_id == Product.id)
            .outerjoin(designer_listing, designer_listing.c.id == Product.primary_listing_id)
            .filter(func.length(designer_label_expr) > 0)
            .group_by(designer_key_expr, designer_label_expr)
            .all()
        )
        return [(str(value), str(label), int(count)) for value, label, count in rows]

    @staticmethod
    def _is_business_source_listing(listing: ProductListing | None) -> bool:
        return listing is not None and str(listing.ingest_mode or "") != "manual"

    @staticmethod
    def _source_mode_for_listing(listing: ProductListing | None) -> str | None:
        if listing is None:
            return None
        source = getattr(listing, "source", None)
        source_key = str(getattr(source, "key", "") or "").strip().lower()
        if source_key == SourceRegistryService.MANUAL_SOURCE_KEY:
            return "personal"
        parser_config = getattr(source, "parser_config", None)
        return SourceRegistryService.normalize_parser_mode((parser_config or {}).get("mode") if isinstance(parser_config, dict) else None)

    @staticmethod
    def _resolved_primary_listing(product: Product) -> ProductListing | None:
        if product.primary_listing is not None:
            return product.primary_listing
        listings = [membership.listing for membership in product.memberships if membership.listing is not None]
        return listings[0] if listings else None

    @staticmethod
    def _dedup_status(product: Product) -> str:
        return str(getattr(product, "dedup_status", "") or "independent").strip().lower() or "independent"

    @classmethod
    def _effective_orderability_state(cls, product: Product, listing: ProductListing | None) -> tuple[str, str | None]:
        dedup_status = cls._dedup_status(product)
        if dedup_status == "combined_source":
            return "unavailable", "dedup_combined_source"
        if dedup_status == "hidden_by_keep":
            return "unavailable", "dedup_hidden_by_keep"
        if listing is None:
            return "unavailable", None
        return (
            str(listing.orderability_status or "unavailable").strip().lower() or "unavailable",
            str(listing.status_reason).strip() if listing.status_reason else None,
        )

    @staticmethod
    def _effective_weight_grams(product: Product, listing: ProductListing | None) -> int | None:
        if product.manual_weight_grams is not None and int(product.manual_weight_grams) > 0:
            return int(product.manual_weight_grams)
        if listing is not None and listing.source_weight_grams is not None and int(listing.source_weight_grams) > 0:
            return int(listing.source_weight_grams)
        if product.weight_rule is not None and product.weight_rule.weight_grams is not None:
            return int(product.weight_rule.weight_grams)
        return None

    @staticmethod
    def _decimal_to_float(value: Decimal | None) -> float | None:
        return float(value) if value is not None else None

    @staticmethod
    def _show_images_enabled(listing: ProductListing | None) -> bool:
        source_setting = getattr(getattr(listing, "source", None), "setting", None) if listing is not None else None
        return bool(getattr(source_setting, "show_images", True))

    @staticmethod
    def _gallery_state(product: Product, listing: ProductListing | None) -> dict:
        if listing is None:
            return {
                "display_image_urls": [],
                "source_image_urls": [],
                "hidden_source_image_urls": [],
                "uploaded_image_urls": [],
                "rows": [],
            }
        show_images_enabled = ProductQueryService._show_images_enabled(listing)
        scope = [
            row
            for row in sorted(product.gallery_images, key=lambda value: (int(value.position), int(value.id or 0)))
            if int(row.listing_id) == int(listing.id)
        ]
        source_urls = [str(image.url) for image in sorted(listing.images, key=lambda value: int(value.position))]
        if not scope:
            if not show_images_enabled:
                return {
                    "display_image_urls": [],
                    "source_image_urls": [],
                    "hidden_source_image_urls": [],
                    "uploaded_image_urls": [],
                    "rows": [],
                }
            return {
                "display_image_urls": list(source_urls),
                "source_image_urls": list(source_urls),
                "hidden_source_image_urls": [],
                "uploaded_image_urls": [],
                "rows": [
                    {
                        "position": index,
                        "origin_kind": "source_image",
                        "is_hidden": False,
                        "url": url,
                    }
                    for index, url in enumerate(source_urls, start=1)
                ],
            }

        display_urls: list[str] = []
        hidden_source_urls: list[str] = []
        uploaded_urls: list[str] = []
        rows: list[dict] = []
        for row in scope:
            url: str | None
            if row.image_asset_id is not None:
                url = f"/api/v1/products/images/{int(row.image_asset_id)}"
                uploaded_urls.append(url)
            else:
                if not show_images_enabled:
                    continue
                url = str(getattr(row.listing_image, "url", "") or "").strip() or None
                if url and row.is_hidden:
                    hidden_source_urls.append(url)
            if url and not row.is_hidden:
                display_urls.append(url)
            rows.append(
                {
                    "position": int(row.position),
                    "origin_kind": str(row.origin_kind),
                    "is_hidden": bool(row.is_hidden),
                    "url": url,
                    "listing_image_id": int(row.listing_image_id) if row.listing_image_id is not None else None,
                    "image_asset_id": int(row.image_asset_id) if row.image_asset_id is not None else None,
                }
            )
        return {
            "display_image_urls": display_urls,
            "source_image_urls": (source_urls if show_images_enabled else []),
            "hidden_source_image_urls": hidden_source_urls,
            "uploaded_image_urls": uploaded_urls,
            "rows": rows,
        }

    @staticmethod
    def _description_state(product: Product, listing: ProductListing | None) -> dict:
        presentation = product.presentation
        source_setting = getattr(getattr(listing, "source", None), "setting", None) if listing is not None else None
        effective_text = (
            str(presentation.description_text)
            if presentation is not None and presentation.description_text
            else (str(listing.source_description_text) if listing is not None and listing.source_description_text else None)
        )
        effective_html = (
            str(presentation.description_html)
            if presentation is not None and presentation.description_html
            else (str(listing.source_description_html) if listing is not None and listing.source_description_html else None)
        )
        description_mode = str(getattr(source_setting, "description_mode", "text") or "text")
        description_mode = description_mode.strip().lower() or "text"
        visibility_override = presentation.description_visibility if presentation is not None else None

        public_description: str | None = None
        is_public_visible = visibility_override is not False
        if description_mode == "hidden":
            is_public_visible = False
        elif is_public_visible:
            if description_mode == "html":
                public_description = effective_html or effective_text
            else:
                public_description = effective_text or effective_html

        return {
            "description_mode": description_mode,
            "visibility_override": visibility_override,
            "public_visible": bool(is_public_visible and public_description is not None),
            "public_description": public_description if is_public_visible else None,
            "effective_text": effective_text,
            "effective_html": effective_html,
        }

    @staticmethod
    def _effective_title(product: Product, listing: ProductListing | None) -> str:
        if product.presentation is not None and product.presentation.title_override:
            return str(product.presentation.title_override)
        if listing is not None and listing.source_title:
            source_brand = ProductQueryService._effective_brand_name(product, listing)
            return (
                ProductTitleService.display_title(
                    source_title=str(listing.source_title),
                    source_designer_name=source_brand,
                    source_category_name=str(listing.source_category_raw or "") or None,
                )
                or str(listing.source_title)
            )
        return f"Product {int(product.id)}"

    @staticmethod
    def _effective_brand_name(product: Product, listing: ProductListing | None) -> str | None:
        presentation = product.presentation
        if presentation is not None and str(getattr(presentation, "brand_override_name", "") or "").strip():
            return str(presentation.brand_override_name)
        if listing is not None and str(listing.source_designer_raw or "").strip():
            return str(listing.source_designer_raw)
        if product.designer is not None and str(product.designer.name or "").strip():
            return str(product.designer.name)
        return None

    @staticmethod
    def _amounts_differ(left: float | int | None, right: float | int | None) -> bool:
        if left is None or right is None:
            return left is not right
        return abs(float(left) - float(right)) >= 0.01

    @staticmethod
    def _variant_summary_sort_key(variant: dict[str, Any]) -> tuple[int, float, float, int, int]:
        final_price = variant.get("final_price")
        source_price = variant.get("price")
        return (
            0 if variant.get("available") else 1,
            float(final_price) if final_price is not None else float("inf"),
            float(source_price) if source_price is not None else float("inf"),
            int(variant.get("listing_id") or 0),
            int(variant.get("position") or 0),
        )

    @staticmethod
    def _range_candidates_for_summary(variants: list[dict[str, Any]], price_key: str) -> list[dict[str, Any]]:
        available = [variant for variant in variants if variant.get("available") and variant.get(price_key) is not None]
        if available:
            return available
        any_priced = [variant for variant in variants if variant.get(price_key) is not None]
        if any_priced:
            return any_priced
        return []

    @classmethod
    def _build_price_summary(cls, variants: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not variants:
            return None
        final_candidates = cls._range_candidates_for_summary(variants, "final_price")
        source_candidates = cls._range_candidates_for_summary(variants, "price")
        representative_pool = final_candidates or source_candidates
        if not representative_pool:
            return None
        representative = min(representative_pool, key=cls._variant_summary_sort_key)

        final_has_range = False
        if len(final_candidates) > 1:
            baseline_final = float(final_candidates[0]["final_price"])
            final_has_range = any(cls._amounts_differ(baseline_final, variant.get("final_price")) for variant in final_candidates[1:])

        source_has_range = False
        if len(source_candidates) > 1:
            baseline_source = source_candidates[0]
            baseline_currency = str(baseline_source.get("currency") or "").upper() or None
            baseline_price = baseline_source.get("price")
            source_has_range = any(
                (str(variant.get("currency") or "").upper() or None) != baseline_currency
                or cls._amounts_differ(baseline_price, variant.get("price"))
                for variant in source_candidates[1:]
            )

        final_display_price = representative.get("final_price")
        final_compare_at_price = representative.get("final_compare_at_price")
        if (
            final_display_price is not None
            and final_compare_at_price is not None
            and not cls._amounts_differ(final_compare_at_price, final_display_price)
        ):
            final_compare_at_price = None
        if (
            final_display_price is not None
            and final_compare_at_price is not None
            and float(final_compare_at_price) < float(final_display_price)
        ):
            final_compare_at_price = None

        return {
            "source_display_price": representative.get("price"),
            "source_currency": representative.get("currency"),
            "source_compare_at_price": representative.get("compare_at_price"),
            "source_has_range": source_has_range,
            "final_display_price": final_display_price,
            "final_currency": representative.get("final_currency"),
            "final_compare_at_price": final_compare_at_price,
            "final_has_range": final_has_range,
            "pricing_reason": representative.get("pricing_reason"),
            "pricing_manual_required": bool(representative.get("pricing_manual_required")),
            "representative_variant_id": representative.get("id"),
            "representative_listing_id": representative.get("listing_id"),
            "representative_source_ref_id": representative.get("source_ref_id"),
        }

    @staticmethod
    def _representative_pricing_components(
        variants: list[dict[str, Any]],
        price_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not variants or not isinstance(price_summary, dict):
            return None
        representative_variant_id = int(price_summary.get("representative_variant_id") or 0)
        representative_listing_id = int(price_summary.get("representative_listing_id") or 0)
        representative_source_ref_id = str(price_summary.get("representative_source_ref_id") or "").strip() or None
        for variant in variants:
            if representative_variant_id > 0 and int(variant.get("id") or 0) != representative_variant_id:
                continue
            if representative_listing_id > 0 and int(variant.get("listing_id") or 0) != representative_listing_id:
                continue
            if representative_source_ref_id and str(variant.get("source_ref_id") or "").strip() != representative_source_ref_id:
                continue
            components = variant.get("pricing_components")
            return dict(components) if isinstance(components, dict) else None
        return None

    def _compute_variant_pricing(
        self,
        listing: ProductListing | None,
        *,
        source_price: float | int | None,
        source_currency: str | None,
        compare_at_price: float | int | None,
        weight_grams: int | None,
        pricing_mode: str | None = None,
    ) -> tuple[float | None, dict | None]:
        if listing is None:
            return None, None
        normalized_currency = str(source_currency or "").upper() or None
        normalized_price = float(source_price) if source_price is not None else None
        normalized_compare_at = float(compare_at_price) if compare_at_price is not None else None
        normalized_pricing_mode = str(pricing_mode or "").strip().lower() or "source"
        cache_key = (
            int(listing.id),
            round(normalized_price, 4) if normalized_price is not None else None,
            normalized_currency,
            round(normalized_compare_at, 4) if normalized_compare_at is not None else None,
            int(weight_grams) if weight_grams is not None else None,
            normalized_pricing_mode,
        )
        cached = self._variant_pricing_cache.get(cache_key)
        if cached is not None:
            return cached
        if normalized_pricing_mode == "fixed_final_rub":
            result = (
                normalized_price,
                {
                    "manual_required": False,
                    "reason": None,
                    "pricing_mode": "fixed_final_rub",
                    "fixed_final_price_rub": normalized_price,
                    "fixed_compare_at_price_rub": normalized_compare_at,
                },
            )
            self._variant_pricing_cache[cache_key] = result
            return result

        supplier_id = getattr(getattr(listing.source, "setting", None), "supplier_id", None)
        promo_factor = getattr(getattr(listing.source, "setting", None), "promo_factor", None)
        promo_only_no_discount = getattr(getattr(listing.source, "setting", None), "promo_only_no_discount", None)
        buyout_surcharge_value = getattr(getattr(listing.source, "setting", None), "buyout_surcharge_value", None)
        buyout_surcharge_currency = getattr(getattr(listing.source, "setting", None), "buyout_surcharge_currency", None)

        try:
            settings = self._pricing_settings()
            computation = self.pricing.calculate_for_product(
                source_price=normalized_price,
                source_currency=normalized_currency,
                weight_grams=weight_grams,
                supplier_id=int(supplier_id) if supplier_id is not None else None,
                promo_factor=float(promo_factor) if promo_factor is not None else None,
                promo_only_no_discount=bool(promo_only_no_discount) if promo_only_no_discount is not None else None,
                buyout_surcharge_value=float(buyout_surcharge_value) if buyout_surcharge_value is not None else None,
                buyout_surcharge_currency=str(buyout_surcharge_currency or "").upper() or None,
                variants=[
                    {
                        "price": normalized_price,
                        "currency": normalized_currency,
                        "compare_at_price": normalized_compare_at,
                        "available": True,
                    }
                ],
                settings=settings,
            )
            result = (
                computation.final_price_rub,
                {
                    "manual_required": computation.manual_required,
                    "reason": computation.reason,
                    **(computation.components or {}),
                },
            )
        except Exception as exc:  # noqa: BLE001
            result = (None, {"manual_required": True, "reason": f"pricing_error:{exc.__class__.__name__}"})

        self._variant_pricing_cache[cache_key] = result
        return result

    def _variant_payload(self, product: Product, listing: ProductListing, variant) -> dict[str, Any]:
        is_business_source = self._is_business_source_listing(listing)
        source_price = self._decimal_to_float(variant.price_amount)
        compare_at_price = self._decimal_to_float(variant.compare_at_price_amount)
        currency = str(variant.currency_code or "").upper() or None
        pricing_mode = str(getattr(variant, "pricing_mode", "") or "source").strip().lower() or "source"
        weight_grams = self._effective_weight_grams(product, listing)
        final_price, pricing_details = self._compute_variant_pricing(
            listing,
            source_price=source_price,
            source_currency=currency,
            compare_at_price=compare_at_price,
            weight_grams=weight_grams,
            pricing_mode=pricing_mode,
        )
        if pricing_mode == "fixed_final_rub":
            final_compare_at_price = compare_at_price
        else:
            final_compare_at_price, _ = self._compute_variant_pricing(
                listing,
                source_price=compare_at_price,
                source_currency=currency,
                compare_at_price=None,
                weight_grams=weight_grams,
                pricing_mode=pricing_mode,
            ) if compare_at_price is not None else (None, None)
        if (
            final_price is not None
            and final_compare_at_price is not None
            and float(final_compare_at_price) <= float(final_price)
        ):
            final_compare_at_price = None

        return {
            "id": int(variant.id),
            "position": int(variant.position),
            "title": str(variant.title or ""),
            "available": bool(variant.is_orderable),
            "price": source_price,
            "inventory_quantity": 1 if bool(variant.is_orderable) else 0,
            "sku": variant.sku,
            "currency": currency,
            "compare_at_price": compare_at_price,
            "pricing_mode": pricing_mode,
            "final_price": final_price,
            "final_currency": "RUB" if final_price is not None else None,
            "final_compare_at_price": final_compare_at_price,
            "final_compare_at_currency": "RUB" if final_compare_at_price is not None else None,
            "pricing_manual_required": bool(pricing_details.get("manual_required")) if isinstance(pricing_details, dict) else False,
            "pricing_reason": (
                str(pricing_details.get("reason") or "").strip() or None
                if isinstance(pricing_details, dict)
                else None
            ),
            "pricing_components": dict(pricing_details) if isinstance(pricing_details, dict) else None,
            "source_id": (int(listing.source_id) if is_business_source else None),
            "source_name": (str(getattr(listing.source, "name", "") or "") or None if is_business_source else None),
            "listing_id": int(listing.id),
            "source_ref_id": str(variant.source_ref_id) if variant.source_ref_id else None,
        }

    def _listing_payload(self, product: Product, listing: ProductListing) -> dict:
        gallery = self._gallery_state(product, listing)
        is_business_source = self._is_business_source_listing(listing)
        return {
            "id": int(listing.id),
            "source_id": (int(listing.source_id) if is_business_source else None),
            "source_name": (
                str(getattr(getattr(listing, "source", None), "name", "") or "") or None
                if is_business_source
                else None
            ),
            "ingest_mode": str(listing.ingest_mode),
            "external_id": str(listing.external_id) if listing.external_id else None,
            "url": (str(listing.url) if is_business_source else None),
            "handle": (str(listing.handle) if is_business_source and listing.handle else None),
            "source_title": str(listing.source_title),
            "source_description_text": str(listing.source_description_text) if listing.source_description_text else None,
            "source_description_html": str(listing.source_description_html) if listing.source_description_html else None,
            "source_weight_grams": int(listing.source_weight_grams) if listing.source_weight_grams is not None else None,
            "source_designer_name": str(listing.source_designer_raw) if listing.source_designer_raw else None,
            "source_category_name": str(listing.source_category_raw) if listing.source_category_raw else None,
            "source_tags": ProductQueryService._normalized_text_list(getattr(listing, "source_tags", None)),
            "orderability_status": str(listing.orderability_status),
            "status_reason": str(listing.status_reason) if listing.status_reason else None,
            "image_urls": gallery["display_image_urls"],
            "gallery": gallery,
            "variants": self._listing_variants(product, listing),
        }

    def _listing_variants(self, product: Product, listing: ProductListing | None) -> list[dict]:
        if listing is None:
            return []
        return [
            self._variant_payload(product, listing, variant)
            for variant in sorted(listing.variants, key=lambda item: int(item.position))
        ]

    def _build_variants(self, product: Product) -> list[dict]:
        variants: list[dict] = []
        for membership in sorted(product.memberships, key=lambda item: int(item.listing_id)):
            listing = membership.listing
            if listing is None:
                continue
            variants.extend(self._listing_variants(product, listing))
        return variants

    def _compute_pricing(self, product: Product, listing: ProductListing | None, variants: list[dict], weight_grams: int | None) -> tuple[float | None, dict | None]:
        if listing is None:
            return None, None
        price_variant = next((variant for variant in variants if variant.get("price") is not None), None)
        if price_variant and str(price_variant.get("pricing_mode") or "").strip().lower() == "fixed_final_rub":
            final_price = float(price_variant["price"]) if price_variant.get("price") is not None else None
            compare_at_price = float(price_variant["compare_at_price"]) if price_variant.get("compare_at_price") is not None else None
            return final_price, {
                "manual_required": False,
                "reason": "fixed_final_rub",
                "pricing_mode": "fixed_final_rub",
                "fixed_final_price_rub": final_price,
                "fixed_compare_at_price_rub": compare_at_price,
            }
        source_price = float(price_variant["price"]) if price_variant and price_variant.get("price") is not None else None
        source_currency = str(price_variant.get("currency") or "").upper() or None if price_variant else None
        supplier_id = getattr(getattr(listing.source, "setting", None), "supplier_id", None)
        promo_factor = getattr(getattr(listing.source, "setting", None), "promo_factor", None)
        promo_only_no_discount = getattr(getattr(listing.source, "setting", None), "promo_only_no_discount", None)
        buyout_surcharge_value = getattr(getattr(listing.source, "setting", None), "buyout_surcharge_value", None)
        buyout_surcharge_currency = getattr(getattr(listing.source, "setting", None), "buyout_surcharge_currency", None)

        try:
            settings = self._pricing_settings()
            computation = self.pricing.calculate_for_product(
                source_price=source_price,
                source_currency=source_currency,
                weight_grams=weight_grams,
                supplier_id=int(supplier_id) if supplier_id is not None else None,
                promo_factor=float(promo_factor) if promo_factor is not None else None,
                promo_only_no_discount=bool(promo_only_no_discount) if promo_only_no_discount is not None else None,
                buyout_surcharge_value=float(buyout_surcharge_value) if buyout_surcharge_value is not None else None,
                buyout_surcharge_currency=str(buyout_surcharge_currency or "").upper() or None,
                variants=variants,
                settings=settings,
            )
            return computation.final_price_rub, {
                "manual_required": computation.manual_required,
                "reason": computation.reason,
                **(computation.components or {}),
            }
        except Exception as exc:  # noqa: BLE001
            return None, {"manual_required": True, "reason": f"pricing_error:{exc.__class__.__name__}"}

    @staticmethod
    def _is_pricing_example_candidate(product: dict[str, Any]) -> bool:
        components = product.get("pricing_components") if isinstance(product.get("pricing_components"), dict) else {}
        if not components:
            return False
        if components.get("manual_required") is True:
            return False
        if str(components.get("reason") or "").strip():
            return False
        required_keys = (
            "source_price_rub",
            "source_price_usd",
            "source_price_eur",
            "buyout_rub",
            "payment_fee_rub",
            "customs_duty_rub",
            "supplier_transport_rub",
            "subtotal_rub",
            "subtotal_after_markup_rub",
            "tax_rub",
            "usdt_extra_rub",
            "bybit_bucket_rate_rub",
            "derived_eur_to_usd_rate",
            "derived_gbp_to_usd_rate",
        )
        if product.get("source_price") is None or product.get("final_price") is None:
            return False
        return all(components.get(key) is not None for key in required_keys)

    def _build_pricing_example_payload_from_product(self, product: Product) -> dict[str, Any] | None:
        listing = product.primary_listing
        if listing is None:
            return None
        if str(product.visibility_status or "") != "visible":
            return None
        if str(listing.orderability_status or "") != "orderable":
            return None
        listing_variants = self._listing_variants(product, listing)
        price_summary = self._build_price_summary(listing_variants)
        effective_weight_grams = self._effective_weight_grams(product, listing)
        final_price, pricing_components = self._compute_pricing(product, listing, listing_variants, effective_weight_grams)

        candidate_payload = {
            "source_price": (
                float(price_summary.get("source_display_price"))
                if isinstance(price_summary, dict) and price_summary.get("source_display_price") is not None
                else None
            ),
            "final_price": (
                float(price_summary.get("final_display_price"))
                if isinstance(price_summary, dict) and price_summary.get("final_display_price") is not None
                else final_price
            ),
            "pricing_components": pricing_components or {},
        }
        if not self._is_pricing_example_candidate(candidate_payload):
            return None

        gallery = self._gallery_state(product, listing)
        return {
            "product_id": int(product.id),
            "title": self._effective_title(product, listing),
            "url": str(listing.url) if self._is_business_source_listing(listing) else None,
            "source_name": (
                str(getattr(getattr(listing, "source", None), "name", "") or "") or None
                if self._is_business_source_listing(listing)
                else None
            ),
            "image_url": (gallery.get("display_image_urls") or [None])[0],
            "price_summary": price_summary,
            "components": pricing_components or {},
            "is_sample": False,
        }

    @staticmethod
    def _sample_weight_grams_from_supplier(
        supplier: PricingSupplierResponse,
        *,
        weight_tolerance: float,
    ) -> int | None:
        rows = PricingSettingsService._normalize_shipping_rows(supplier.rates)
        if not rows:
            return None
        row = rows[0]
        min_kg = max(0.0, float(row.get("min_kg") or 0.0))
        max_kg_raw = row.get("max_kg")
        max_kg = float(max_kg_raw) if max_kg_raw is not None else None
        if max_kg is None:
            billable_kg = max(min_kg, 1.0)
        elif max_kg > min_kg:
            billable_kg = max(0.1, (min_kg + max_kg) / 2.0)
        else:
            billable_kg = max(min_kg, 0.1)
        safe_tolerance = max(0.1, float(weight_tolerance or 1.0))
        return max(1, int(round((billable_kg / safe_tolerance) * 1000.0)))

    @classmethod
    def _build_sample_pricing_example_payload(cls, settings: PricingSettingsResponse) -> dict[str, Any] | None:
        suppliers = [
            supplier
            for supplier in sorted(
                list(settings.suppliers or []),
                key=lambda item: (
                    1 if str(item.provider_kind or "").strip().lower() != "main" else 0,
                    int(item.id),
                ),
            )
            if bool(getattr(supplier, "is_enabled", True))
        ]
        supplier_with_tariff = next(
            (
                supplier
                for supplier in suppliers
                if PricingSettingsService._normalize_shipping_rows(supplier.rates)
            ),
            None,
        )
        sample_supplier: PricingSupplierResponse
        sample_source_name: str
        if supplier_with_tariff is not None:
            sample_supplier = supplier_with_tariff
            sample_source_name = f"Типовой пример · {supplier_with_tariff.name}"
        elif suppliers:
            sample_supplier = suppliers[0].model_copy(
                update={
                    "rates": [PricingSupplierRateResponse(min_kg=0.0, max_kg=None, rub=0.0)],
                }
            )
            sample_source_name = f"Типовой пример · {sample_supplier.name} (тариф еще не настроен)"
        else:
            sample_supplier = PricingSupplierResponse(
                id=0,
                key="sample-supplier",
                name="Доставка не настроена",
                provider_kind="main",
                parent_supplier_id=None,
                rate_currency="RUB",
                is_enabled=True,
                rates=[PricingSupplierRateResponse(min_kg=0.0, max_kg=None, rub=0.0)],
            )
            sample_source_name = "Типовой пример · доставка не настроена"

        sample_settings = settings.model_copy(
            update={"suppliers": [sample_supplier]},
            deep=True,
        )
        sample_weight_grams = cls._sample_weight_grams_from_supplier(
            sample_supplier,
            weight_tolerance=float(sample_settings.weight_tolerance),
        )
        if sample_weight_grams is None:
            return None
        sample_source_price = max(50.0, float(sample_settings.customs_threshold_eur) + 50.0)
        computation = PricingSettingsService.calculate_for_product(
            source_price=sample_source_price,
            source_currency="EUR",
            weight_grams=sample_weight_grams,
            supplier_id=int(sample_supplier.id),
            promo_factor=1.0,
            promo_only_no_discount=False,
            buyout_surcharge_value=None,
            buyout_surcharge_currency=None,
            variants=[],
            settings=sample_settings,
        )
        if computation.manual_required or computation.final_price_rub is None:
            return None
        price_summary = cls._build_price_summary(
            [
                {
                    "price": sample_source_price,
                    "currency": "EUR",
                    "compare_at_price": None,
                    "final_price": computation.final_price_rub,
                    "final_currency": "RUB",
                    "final_compare_at_price": None,
                    "pricing_manual_required": bool(computation.manual_required),
                    "pricing_reason": computation.reason,
                    "pricing_components": computation.components or {},
                    "available": True,
                }
            ]
        )
        return {
            "product_id": None,
            "title": "Пример расчета",
            "url": None,
            "source_name": sample_source_name,
            "image_url": None,
            "price_summary": price_summary,
            "components": computation.components or {},
            "is_sample": True,
        }

    def get_pricing_example_payload(self, *, product_id: int | None = None) -> dict[str, Any] | None:
        if product_id is not None and int(product_id) > 0:
            products = self.products.list_products_for_pricing_example_by_ids([int(product_id)])
            if products:
                return self._build_pricing_example_payload_from_product(products[0])
            return None

        product_ids = [
            int(row[0])
            for row in (
                self.db.query(Product.id)
                .join(ProductListing, ProductListing.id == Product.primary_listing_id)
                .filter(Product.lifecycle_status == "active")
                .filter(Product.dedup_status == "independent")
                .filter(Product.primary_listing_id.is_not(None))
                .filter(Product.visibility_status == "visible")
                .filter(ProductListing.orderability_status == "orderable")
                .order_by(func.random())
                .limit(self._PRICING_EXAMPLE_SCAN_LIMIT)
                .all()
            )
        ]

        batch_size = max(1, int(self._PRICING_EXAMPLE_BATCH_SIZE))
        for start in range(0, len(product_ids), batch_size):
            batch_ids = product_ids[start : start + batch_size]
            products = self.products.list_products_for_pricing_example_by_ids(batch_ids)
            product_by_id = {int(product.id): product for product in products}
            for product_id in batch_ids:
                product = product_by_id.get(product_id)
                if product is None:
                    continue
                payload = self._build_pricing_example_payload_from_product(product)
                if payload is not None:
                    return payload

        settings = self._pricing_settings()
        return self._build_sample_pricing_example_payload(settings)

    def _build_shared_payload(self, product: Product) -> tuple[dict, dict]:
        primary_listing = self._resolved_primary_listing(product)
        effective_orderability_status, effective_status_reason = self._effective_orderability_state(product, primary_listing)
        primary_is_business_source = self._is_business_source_listing(primary_listing)
        title = self._effective_title(product, primary_listing)
        description = self._description_state(product, primary_listing)
        gallery = self._gallery_state(product, primary_listing)
        variants = self._build_variants(product)
        primary_listing_variants = self._listing_variants(product, primary_listing)
        effective_weight_grams = self._effective_weight_grams(product, primary_listing)
        final_price, pricing_components = self._compute_pricing(product, primary_listing, primary_listing_variants, effective_weight_grams)
        price_summary = self._build_price_summary(variants)
        representative_pricing_components = self._representative_pricing_components(variants, price_summary)
        matched_filter_slugs = self._matched_filter_slugs(product)
        custom_catalog_slugs = self._custom_catalog_slugs(int(product.id))
        matched_filter_labels = self._matched_filter_labels(product)
        custom_catalog_names = self._taxonomy_titles(filter_slugs=[], custom_catalog_slugs=custom_catalog_slugs)
        internal_category_names = list(matched_filter_labels)
        for title in custom_catalog_names:
            if title not in internal_category_names:
                internal_category_names.append(title)
        showcase_category_codes = self._showcase_category_codes_for_product(
            matched_filter_slugs=matched_filter_slugs,
            custom_catalog_slugs=custom_catalog_slugs,
        )
        listings = [
            self._listing_payload(product, listing)
            for listing in self.products.list_product_listings(int(product.id))
        ]
        has_sync_listing = any(str(listing.get("ingest_mode") or "").strip() == "sync" for listing in listings)

        base_payload = {
            "id": int(product.id),
            "designer_id": int(product.designer_id) if product.designer_id is not None else None,
            "designer_name": str(getattr(product.designer, "name", "") or "") or None,
            "display_designer_name": str(getattr(product.designer, "name", "") or "") or self._effective_brand_name(product, primary_listing),
            "brand_name": self._effective_brand_name(product, primary_listing),
            "brand_name_is_manual": bool(
                product.presentation is not None
                and str(getattr(product.presentation, "brand_override_name", "") or "").strip()
            ),
            "primary_listing_id": int(primary_listing.id) if primary_listing is not None else None,
            "gender": str(product.gender),
            "gender_is_manual": bool(getattr(product, "gender_is_manual", False)),
            "availability_mode": str(product.availability_mode),
            "visibility_status": str(product.visibility_status),
            "lifecycle_status": str(product.lifecycle_status),
            "manual_weight_grams": int(product.manual_weight_grams) if product.manual_weight_grams is not None else None,
            "auto_weight_grams": int(product.weight_rule.weight_grams) if product.weight_rule is not None and product.weight_rule.weight_grams is not None else None,
            "weight_rule_id": int(product.weight_rule_id) if product.weight_rule_id is not None else None,
            "effective_weight_grams": effective_weight_grams,
            "title": title,
            "url": (
                str(primary_listing.url)
                if primary_listing is not None and primary_is_business_source
                else None
            ),
            "handle": (
                str(primary_listing.handle)
                if primary_listing is not None and primary_is_business_source and primary_listing.handle
                else None
            ),
            "source_id": (
                int(primary_listing.source_id)
                if primary_listing is not None and primary_is_business_source
                else None
            ),
            "source_name": (
                str(getattr(getattr(primary_listing, "source", None), "name", "") or "") or None
                if primary_listing is not None and primary_is_business_source
                else None
            ),
            "source_mode": self._source_mode_for_listing(primary_listing),
            "has_sync_listing": has_sync_listing,
            "source_category_name": str(primary_listing.source_category_raw) if primary_listing is not None and primary_listing.source_category_raw else None,
            "source_tags": self._normalized_text_list(getattr(primary_listing, "source_tags", None)) if primary_listing is not None else [],
            "source_designer_name": str(primary_listing.source_designer_raw) if primary_listing is not None and primary_listing.source_designer_raw else None,
            "orderability_status": effective_orderability_status,
            "status_reason": effective_status_reason,
            "description_mode": description["description_mode"],
            "description": description["public_description"],
            "price_summary": price_summary,
            "pricing_reason": (
                str(price_summary.get("pricing_reason") or "").strip() or None
                if isinstance(price_summary, dict)
                else None
            ),
            "pricing_manual_required": (
                bool(price_summary.get("pricing_manual_required"))
                if isinstance(price_summary, dict)
                else False
            ),
            "pricing_components": representative_pricing_components or pricing_components,
            "image_urls": gallery["display_image_urls"],
            "variants": variants,
            "internal_category_names": internal_category_names,
            "internal_category_name": internal_category_names[0] if internal_category_names else None,
            "filter_name": matched_filter_labels[0] if matched_filter_labels else None,
            "custom_catalog_names": custom_catalog_names,
            "created_at": product.created_at.isoformat() if product.created_at else None,
            "updated_at": product.updated_at.isoformat() if product.updated_at else None,
        }
        admin_extras = {
            "description_text": description["effective_text"],
            "description_html": description["effective_html"],
            "description_public_visible": description["public_visible"],
            "gallery": gallery,
            "presentation": {
                "title_override": getattr(product.presentation, "title_override", None) if product.presentation is not None else None,
                "brand_override_name": getattr(product.presentation, "brand_override_name", None) if product.presentation is not None else None,
                "description_text": getattr(product.presentation, "description_text", None) if product.presentation is not None else None,
                "description_html": getattr(product.presentation, "description_html", None) if product.presentation is not None else None,
                "description_visibility": getattr(product.presentation, "description_visibility", None) if product.presentation is not None else None,
            },
            "taxonomy": {
                "filter_slugs": matched_filter_slugs,
                "custom_catalog_slugs": custom_catalog_slugs,
                "showcase_category_codes": showcase_category_codes,
            },
            "listings": listings,
        }
        return base_payload, admin_extras

    def build_admin_product_payload(self, product: Product) -> dict:
        base_payload, admin_extras = self._build_shared_payload(product)
        return {**base_payload, **admin_extras}

    def build_public_product_payload(self, product: Product) -> dict:
        base_payload, _ = self._build_shared_payload(product)
        return base_payload

    def build_dedup_candidate_payload(self, product: Product) -> dict:
        primary_listing = self._resolved_primary_listing(product)
        effective_orderability_status, effective_status_reason = self._effective_orderability_state(product, primary_listing)
        image_urls = (
            [str(primary_listing.images[0].url)]
            if primary_listing is not None and self._show_images_enabled(primary_listing) and primary_listing.images
            else []
        )
        variants = self._build_variants(product)
        return {
            "id": int(product.id),
            "title": self._effective_title(product, primary_listing),
            "designer_name": str(getattr(product.designer, "name", "") or "") or None,
            "source_designer_name": str(primary_listing.source_designer_raw) if primary_listing is not None and primary_listing.source_designer_raw else None,
            "display_designer_name": (
                str(getattr(product.designer, "name", "") or "")
                or self._effective_brand_name(product, primary_listing)
            ),
            "url": (
                str(primary_listing.url)
                if primary_listing is not None and self._is_business_source_listing(primary_listing)
                else None
            ),
            "price_summary": self._build_price_summary(variants),
            "visibility_status": str(product.visibility_status),
            "orderability_status": effective_orderability_status,
            "status_reason": effective_status_reason,
            "effective_weight_grams": self._effective_weight_grams(product, primary_listing),
            "image_urls": image_urls,
            "image_ids": [],
            "image_count": len(image_urls),
            "listings": [
                {
                    "id": int(listing.id),
                    "source_name": (
                        str(getattr(getattr(listing, "source", None), "name", "") or "") or None
                        if self._is_business_source_listing(listing)
                        else None
                    ),
                }
                for listing in [
                    membership.listing
                    for membership in sorted(product.memberships, key=lambda item: int(item.listing_id))
                    if membership.listing is not None
                ]
            ],
        }

    def build_dedup_decision_payload(self, product: Product) -> dict:
        return self.build_dedup_candidate_payload(product)

    def build_product_payload(self, product: Product, *, audience: str = "admin") -> dict:
        if str(audience).strip().lower() == "public":
            return self.build_public_product_payload(product)
        return self.build_admin_product_payload(product)

    def _base_product_id_query(
        self,
        *,
        query: str = "",
        source_id: int | None = None,
        source_mode: str | None = None,
        designer_filter: str | None = None,
        gender: str | None = None,
        filter_slug: str | None = None,
        custom_catalog_slug: str | None = None,
        visibility_status: str | None = None,
        availability_mode: str | None = None,
        orderability_status: str | None = None,
        audience: str = "admin",
    ):
        normalized_source_mode = str(source_mode or "").strip().lower()
        normalized_designer_filter = str(designer_filter or "").strip()
        normalized_query = " ".join(str(query or "").strip().lower().split())
        effective_filter_slug = str(filter_slug or "").strip() or None
        effective_custom_catalog_slug = str(custom_catalog_slug or "").strip() or None

        needs_listing_join = any(
            (
                source_id is not None,
                bool(normalized_source_mode),
                bool(orderability_status),
                bool(normalized_query),
                effective_filter_slug is not None,
                bool(normalized_designer_filter and not normalized_designer_filter.isdigit()),
            )
        )
        needs_presentation_join = bool(normalized_query or (normalized_designer_filter and not normalized_designer_filter.isdigit()))
        normalized_audience = str(audience or "admin").strip().lower()
        base_query = (
            self.db.query(Product.id.label("product_id"))
            .filter(Product.lifecycle_status == "active")
            .filter(Product.primary_listing_id.is_not(None))
        )
        if normalized_audience == "public":
            base_query = base_query.filter(Product.dedup_status == "independent")
        if needs_listing_join:
            base_query = (
                base_query
                .join(ProductListingMember, ProductListingMember.product_id == Product.id)
                .join(ProductListing, ProductListing.id == ProductListingMember.listing_id)
            )
        if needs_presentation_join:
            base_query = base_query.outerjoin(ProductPresentation, ProductPresentation.product_id == Product.id)

        if source_id is not None:
            base_query = base_query.filter(ProductListing.source_id == int(source_id))
        if normalized_source_mode:
            matching_source_ids = self._source_ids_for_mode(normalized_source_mode)
            if not matching_source_ids:
                return base_query.filter(False)
            base_query = base_query.filter(ProductListing.source_id.in_(matching_source_ids))
        if normalized_designer_filter:
            if normalized_designer_filter.isdigit():
                base_query = base_query.filter(Product.designer_id == int(normalized_designer_filter))
            else:
                raw_designer_name = normalized_designer_filter[5:] if normalized_designer_filter.lower().startswith("name:") else normalized_designer_filter
                designer_name = " ".join(raw_designer_name.strip().lower().split())
                if designer_name:
                    base_query = base_query.filter(
                        func.lower(
                            func.coalesce(ProductPresentation.brand_override_name, ProductListing.source_designer_raw, "")
                        ) == designer_name
                    )
                else:
                    base_query = base_query.filter(False)
        if visibility_status:
            base_query = base_query.filter(Product.visibility_status == str(visibility_status).strip().lower())
        if gender:
            base_query = base_query.filter(Product.gender == str(gender).strip().lower())
        if availability_mode:
            base_query = base_query.filter(Product.availability_mode == str(availability_mode).strip().lower())
        if orderability_status:
            normalized_orderability_status = str(orderability_status).strip().lower()
            effective_orderability_expr = case(
                (Product.dedup_status != "independent", literal("unavailable")),
                else_=func.coalesce(ProductListing.orderability_status, literal("unavailable")),
            )
            base_query = base_query.filter(effective_orderability_expr == normalized_orderability_status)
        if effective_filter_slug is not None:
            base_query = self._apply_filter_slug_query(base_query, effective_filter_slug)
        if effective_custom_catalog_slug is not None:
            base_query = (
                base_query.join(CustomCatalogProduct, CustomCatalogProduct.product_id == Product.id)
                .join(CustomCatalog, CustomCatalog.id == CustomCatalogProduct.catalog_id)
                .filter(CustomCatalog.slug == effective_custom_catalog_slug)
            )

        if normalized_query:
            pattern = f"%{normalized_query}%"
            base_query = base_query.filter(
                or_(
                    func.lower(func.coalesce(ProductListing.source_title, "")).like(pattern),
                    func.lower(func.coalesce(ProductListing.source_description_text, "")).like(pattern),
                    func.lower(func.coalesce(ProductListing.source_description_html, "")).like(pattern),
                    func.lower(func.coalesce(ProductPresentation.brand_override_name, ProductListing.source_designer_raw, "")).like(pattern),
                    func.lower(func.coalesce(ProductListing.source_category_raw, "")).like(pattern),
                    func.lower(func.coalesce(ProductListing.handle, "")).like(pattern),
                    func.lower(func.coalesce(ProductListing.url, "")).like(pattern),
                    func.lower(func.coalesce(ProductPresentation.title_override, "")).like(pattern),
                    func.lower(func.coalesce(ProductPresentation.description_text, "")).like(pattern),
                    func.lower(func.coalesce(ProductPresentation.description_html, "")).like(pattern),
                )
            )
        return base_query

    def _source_ids_for_mode(self, source_mode: str) -> list[int]:
        normalized_mode = str(source_mode or "").strip().lower()
        if normalized_mode not in {"auto", "manual", "personal"}:
            return []
        cached_ids = self._source_mode_ids_cache.get(normalized_mode)
        if cached_ids is not None:
            return list(cached_ids)
        source_ids = [
            int(source_id)
            for source_id, source_key, _source_name, source_mode in self._source_rows()
            if ("personal" if source_key == SourceRegistryService.MANUAL_SOURCE_KEY else source_mode) == normalized_mode
        ]
        self._source_mode_ids_cache[normalized_mode] = list(source_ids)
        return list(source_ids)

    def list_products(
        self,
        *,
        limit: int,
        offset: int,
        query: str = "",
        source_id: int | None = None,
        source_mode: str | None = None,
        designer_filter: str | None = None,
        gender: str | None = None,
        filter_slug: str | None = None,
        custom_catalog_slug: str | None = None,
        visibility_status: str | None = None,
        availability_mode: str | None = None,
        orderability_status: str | None = None,
        audience: str = "admin",
    ) -> dict:
        filtered_ids = self._filtered_product_ids_subquery(
            query=query,
            source_id=source_id,
            source_mode=source_mode,
            designer_filter=designer_filter,
            gender=gender,
            filter_slug=filter_slug,
            custom_catalog_slug=custom_catalog_slug,
            visibility_status=visibility_status,
            availability_mode=availability_mode,
            orderability_status=orderability_status,
            audience=audience,
            alias="public_product_ids",
        )
        total = int(self.db.query(func.count()).select_from(filtered_ids).scalar() or 0)
        product_ids = [
            int(row[0])
            for row in (
                self.db.query(filtered_ids.c.product_id)
                .join(Product, Product.id == filtered_ids.c.product_id)
                .order_by(Product.updated_at.desc(), Product.id.desc())
                .offset(max(0, int(offset)))
                .limit(max(1, int(limit)))
                .all()
            )
        ]
        products = self.products.list_products_by_ids(product_ids, include_merged=False)
        product_by_id = {int(product.id): product for product in products}
        items = [
            self.build_product_payload(product_by_id[product_id], audience=audience)
            for product_id in product_ids
            if product_id in product_by_id
        ]
        return {
            "items": items,
            "total": total,
            "limit": int(limit),
            "offset": int(offset),
        }

    def get_product_payload(self, product_id: int, *, audience: str = "admin") -> dict | None:
        product = self.products.get_product(product_id)
        if product is None:
            return None
        normalized_audience = str(audience).strip().lower()
        if normalized_audience == "public" and self._dedup_status(product) != "independent":
            return None
        primary_listing = self._resolved_primary_listing(product)
        effective_orderability_status, _ = self._effective_orderability_state(product, primary_listing)
        if effective_orderability_status == "unavailable":
            return None
        return self.build_product_payload(product, audience=normalized_audience)

    def get_admin_mutation_payload(self, product_id: int) -> dict | None:
        product = self.products.get_product(product_id)
        if product is None:
            return None
        return self.build_admin_product_payload(product)

    def get_dedup_payloads_by_ids(self, product_ids: list[int]) -> dict[int, dict]:
        products = self.products.list_products_for_dedup_by_ids(product_ids)
        return {
            int(product.id): self.build_dedup_decision_payload(product)
            for product in products
        }

    def _table_custom_catalog_titles_by_product_ids(self, product_ids: list[int]) -> dict[int, list[str]]:
        if not product_ids:
            return {}
        rows = (
            self.db.query(CustomCatalogProduct.product_id, CustomCatalog.title)
            .join(CustomCatalog, CustomCatalog.id == CustomCatalogProduct.catalog_id)
            .filter(CustomCatalogProduct.product_id.in_(product_ids))
            .order_by(CustomCatalogProduct.product_id.asc(), CustomCatalog.title.asc(), CustomCatalog.id.asc())
            .all()
        )
        result: dict[int, list[str]] = {}
        for product_id, title in rows:
            normalized_title = str(title or "").strip()
            if not normalized_title:
                continue
            bucket = result.setdefault(int(product_id), [])
            if normalized_title not in bucket:
                bucket.append(normalized_title)
        return result

    def build_admin_table_product_payload(self, product: Product, *, custom_catalog_titles: list[str] | None = None) -> dict:
        primary_listing = self._resolved_primary_listing(product)
        effective_orderability_status, effective_status_reason = self._effective_orderability_state(product, primary_listing)
        variants = self._build_variants(product)
        price_summary = self._build_price_summary(variants)
        gallery = self._gallery_state(product, primary_listing)
        internal_category_names = self._matched_filter_labels(product)
        for title in custom_catalog_titles or []:
            normalized_title = str(title or "").strip()
            if normalized_title and normalized_title not in internal_category_names:
                internal_category_names.append(normalized_title)

        return {
            "id": int(product.id),
            "source_id": (
                int(primary_listing.source_id)
                if primary_listing is not None and self._is_business_source_listing(primary_listing)
                else None
            ),
            "source_name": (
                str(getattr(getattr(primary_listing, "source", None), "name", "") or "") or None
                if primary_listing is not None and self._is_business_source_listing(primary_listing)
                else None
            ),
            "title": self._effective_title(product, primary_listing),
            "gender": str(product.gender),
            "designer_name": str(getattr(product.designer, "name", "") or "") or None,
            "source_designer_name": str(primary_listing.source_designer_raw) if primary_listing is not None and primary_listing.source_designer_raw else None,
            "display_designer_name": (
                str(getattr(product.designer, "name", "") or "")
                or (str(primary_listing.source_designer_raw) if primary_listing is not None and primary_listing.source_designer_raw else None)
            ),
            "url": (
                str(primary_listing.url)
                if primary_listing is not None and self._is_business_source_listing(primary_listing)
                else None
            ),
            "source_category_name": str(primary_listing.source_category_raw) if primary_listing is not None and primary_listing.source_category_raw else None,
            "source_tags": self._normalized_text_list(getattr(primary_listing, "source_tags", None)) if primary_listing is not None else [],
            "visibility_status": str(product.visibility_status),
            "availability_mode": str(product.availability_mode),
            "orderability_status": effective_orderability_status,
            "status_reason": effective_status_reason,
            "lifecycle_status": str(product.lifecycle_status),
            "image_count": len(gallery["display_image_urls"]),
            "image_urls": list(gallery["display_image_urls"][:1]),
            "image_ids": [],
            "price_summary": price_summary,
            "pricing_reason": (
                str(price_summary.get("pricing_reason") or "").strip() or None
                if isinstance(price_summary, dict)
                else None
            ),
            "pricing_manual_required": (
                bool(price_summary.get("pricing_manual_required"))
                if isinstance(price_summary, dict)
                else False
            ),
            "internal_category_name": internal_category_names[0] if internal_category_names else None,
            "internal_category_names": internal_category_names,
        }

    def search_products(self, *, query: str, limit: int, offset: int) -> dict:
        return self.list_products(limit=limit, offset=offset, query=query, audience="admin")

    def list_admin_table_products(
        self,
        *,
        limit: int,
        offset: int,
        query: str = "",
        source_id: int | None = None,
        source_mode: str | None = None,
        designer_filter: str | None = None,
        gender: str | None = None,
        filter_slug: str | None = None,
        custom_catalog_slug: str | None = None,
        visibility_status: str | None = None,
        availability_mode: str | None = None,
        orderability_status: str | None = None,
    ) -> dict:
        filtered_ids = self._filtered_product_ids_subquery(
            query=query,
            source_id=source_id,
            source_mode=source_mode,
            designer_filter=designer_filter,
            gender=gender,
            filter_slug=filter_slug,
            custom_catalog_slug=custom_catalog_slug,
            visibility_status=visibility_status,
            availability_mode=availability_mode,
            orderability_status=orderability_status,
            alias="admin_table_product_ids",
        )
        total = int(self.db.query(func.count()).select_from(filtered_ids).scalar() or 0)
        product_ids = [
            int(row[0])
            for row in (
                self.db.query(filtered_ids.c.product_id)
                .join(Product, Product.id == filtered_ids.c.product_id)
                .order_by(Product.updated_at.desc(), Product.id.desc())
                .offset(max(0, int(offset)))
                .limit(max(1, int(limit)))
                .all()
            )
        ]
        products = self.products.list_products_for_admin_table_by_ids(product_ids)
        product_by_id = {int(product.id): product for product in products}
        custom_catalog_titles_by_product_id = self._table_custom_catalog_titles_by_product_ids(product_ids)
        items = [
            self.build_admin_table_product_payload(
                product_by_id[product_id],
                custom_catalog_titles=custom_catalog_titles_by_product_id.get(product_id, []),
            )
            for product_id in product_ids
            if product_id in product_by_id
        ]
        return {
            "items": items,
            "total": total,
            "limit": int(limit),
            "offset": int(offset),
        }

    def admin_table_facets(
        self,
        *,
        query: str = "",
        source_id: int | None = None,
        source_mode: str | None = None,
        designer_filter: str | None = None,
        gender: str | None = None,
        filter_slug: str | None = None,
        custom_catalog_slug: str | None = None,
        visibility_status: str | None = None,
        availability_mode: str | None = None,
        orderability_status: str | None = None,
    ) -> dict:
        effective_filter_slug = str(filter_slug or "").strip() or None
        section_context_ids = self._filtered_product_ids_subquery(
            query=query,
            source_id=source_id,
            source_mode=source_mode,
            designer_filter=designer_filter,
            gender=gender,
            custom_catalog_slug=custom_catalog_slug,
            visibility_status=visibility_status,
            availability_mode=availability_mode,
            orderability_status=orderability_status,
            alias="facet_section_products",
        )
        total_ids = self._filtered_product_ids_subquery(
            query=query,
            source_id=source_id,
            source_mode=source_mode,
            designer_filter=designer_filter,
            gender=gender,
            filter_slug=effective_filter_slug,
            custom_catalog_slug=custom_catalog_slug,
            visibility_status=visibility_status,
            availability_mode=availability_mode,
            orderability_status=orderability_status,
            alias="facet_total_products",
        )
        source_context_ids = self._filtered_product_ids_subquery(
            query=query,
            source_mode=source_mode,
            designer_filter=designer_filter,
            gender=gender,
            filter_slug=effective_filter_slug,
            custom_catalog_slug=custom_catalog_slug,
            visibility_status=visibility_status,
            availability_mode=availability_mode,
            orderability_status=orderability_status,
            alias="facet_source_products",
        )
        designer_context_ids = self._filtered_product_ids_subquery(
            query=query,
            source_id=source_id,
            source_mode=source_mode,
            gender=gender,
            filter_slug=effective_filter_slug,
            custom_catalog_slug=custom_catalog_slug,
            visibility_status=visibility_status,
            availability_mode=availability_mode,
            orderability_status=orderability_status,
            alias="facet_designer_products",
        )
        catalog_context_ids = self._filtered_product_ids_subquery(
            query=query,
            source_id=source_id,
            source_mode=source_mode,
            designer_filter=designer_filter,
            gender=gender,
            filter_slug=effective_filter_slug,
            visibility_status=visibility_status,
            availability_mode=availability_mode,
            orderability_status=orderability_status,
            alias="facet_catalog_products",
        )
        gender_context_ids = self._filtered_product_ids_subquery(
            query=query,
            source_id=source_id,
            source_mode=source_mode,
            designer_filter=designer_filter,
            filter_slug=effective_filter_slug,
            custom_catalog_slug=custom_catalog_slug,
            visibility_status=visibility_status,
            availability_mode=availability_mode,
            orderability_status=orderability_status,
            alias="facet_gender_products",
        )
        total = int(self.db.query(func.count()).select_from(total_ids).scalar() or 0)
        source_counts = {
            int(source_row.source_id): int(source_row.product_count)
            for source_row in (
                self.db.query(
                    ProductListing.source_id.label("source_id"),
                    func.count(func.distinct(source_context_ids.c.product_id)).label("product_count"),
                )
                .select_from(source_context_ids)
                .join(ProductListingMember, ProductListingMember.product_id == source_context_ids.c.product_id)
                .join(ProductListing, ProductListing.id == ProductListingMember.listing_id)
                .filter(ProductListing.source_id.is_not(None))
                .group_by(ProductListing.source_id)
                .all()
            )
        }
        designer_count_rows = self._designer_option_rows(designer_context_ids)
        catalog_count_map = {
            str(slug): int(count)
            for slug, count in (
                self.db.query(
                    CustomCatalog.slug.label("slug"),
                    func.count(func.distinct(catalog_context_ids.c.product_id)).label("product_count"),
                )
                .select_from(CustomCatalog)
                .outerjoin(CustomCatalogProduct, CustomCatalogProduct.catalog_id == CustomCatalog.id)
                .outerjoin(catalog_context_ids, catalog_context_ids.c.product_id == CustomCatalogProduct.product_id)
                .group_by(CustomCatalog.slug, CustomCatalog.id)
                .all()
            )
            if str(slug or "").strip()
        }
        gender_count_map = {
            str(value): int(count)
            for value, count in (
                self.db.query(Product.gender, func.count())
                .select_from(gender_context_ids)
                .join(Product, Product.id == gender_context_ids.c.product_id)
                .group_by(Product.gender)
                .all()
            )
            if str(value or "").strip()
        }
        source_options = []
        for source_id_value, source_key_value, source_name_value, parser_mode_value in self._source_rows():
            source_key = int(source_id_value)
            count = int(source_counts.get(source_key, 0))
            source_mode_value = "personal" if source_key_value == SourceRegistryService.MANUAL_SOURCE_KEY else parser_mode_value
            source_options.append(
                {
                    "value": str(source_key),
                    "label": source_name_value or source_key_value or str(source_key),
                    "count": count,
                    "disabled": count == 0,
                    "_mode": source_mode_value,
                }
            )
        source_options.sort(
            key=lambda item: (
                0 if str(item.get("_mode")) == "personal" else 1 if str(item.get("_mode")) == "manual" else 2,
                str(item["label"]).lower(),
                str(item["value"]),
            )
        )
        for item in source_options:
            item.pop("_mode", None)

        all_active_product_ids = (
            self.db.query(Product.id.label("product_id"))
            .filter(Product.lifecycle_status == "active")
            .subquery("facet_all_active_products")
        )
        designer_count_map = {
            value: count
            for value, _, count in designer_count_rows
        }
        designer_label_map: dict[str, str] = {}
        for value, label, _ in self._designer_option_rows(all_active_product_ids):
            if label and value not in designer_label_map:
                designer_label_map[value] = label
        designers = [
            {
                "value": value,
                "label": label,
                "count": int(designer_count_map.get(value, 0)),
                "disabled": int(designer_count_map.get(value, 0)) == 0,
            }
            for value, label in designer_label_map.items()
        ]
        designers.sort(key=lambda item: (int(bool(item["disabled"])), -int(item["count"]), str(item["label"]).lower()))

        catalogs = [
            {
                "value": slug,
                "label": title,
                "count": int(catalog_count_map.get(slug, 0)),
                "disabled": int(catalog_count_map.get(slug, 0)) == 0,
            }
            for slug, title in self._custom_catalog_rows()
        ]
        catalogs.sort(key=lambda item: (int(bool(item["disabled"])), -int(item["count"]), str(item["label"]).lower()))
        section_total = int(self.db.query(func.count()).select_from(section_context_ids).scalar() or 0)
        section_count_map: dict[str, int] = {}
        revision = self._applied_filter_revision()
        if revision > 0:
            section_count_map = {
                str(slug): int(count)
                for slug, count in (
                    self.db.query(
                        ProductFilterAssignment.filter_slug,
                        func.count(func.distinct(section_context_ids.c.product_id)).label("product_count"),
                    )
                    .select_from(section_context_ids)
                    .join(
                        ProductFilterAssignment,
                        and_(
                            ProductFilterAssignment.product_id == section_context_ids.c.product_id,
                            ProductFilterAssignment.revision == revision,
                        ),
                    )
                    .group_by(ProductFilterAssignment.filter_slug)
                    .all()
                )
                if str(slug or "").strip()
            }
        unmatched_section_count = max(0, section_total - sum(section_count_map.values()))
        sections = []
        sections.append(
            {
                "value": UNMATCHED_FILTER_SLUG,
                "label": UNMATCHED_FILTER_LABEL,
                "count": unmatched_section_count,
                "disabled": unmatched_section_count == 0,
            }
        )
        for slug, label, is_enabled in self._filter_rows():
            if not is_enabled:
                continue
            count = int(section_count_map.get(slug, 0))
            sections.append(
                {
                    "value": slug,
                    "label": label,
                    "count": count,
                    "disabled": count == 0,
                }
            )
        sections.sort(
            key=lambda item: (
                0 if str(item["value"]) == UNMATCHED_FILTER_SLUG else 1,
                int(bool(item["disabled"])),
                -int(item["count"]),
                str(item["label"]).lower(),
            )
        )

        genders = [
            {
                "value": value,
                "label": label,
                "count": int(gender_count_map.get(value, 0)),
                "disabled": int(gender_count_map.get(value, 0)) == 0,
            }
            for value, label in (
                ("male", "Мужской"),
                ("female", "Женский"),
                ("unisex", "Унисекс"),
            )
        ]
        genders.sort(key=lambda item: (int(bool(item["disabled"])), -int(item["count"]), str(item["label"]).lower()))

        overall_total = int(
            self.db.query(func.count(Product.id))
            .filter(Product.lifecycle_status == "active")
            .scalar()
            or 0
        )
        return {
            "sources": source_options,
            "designers": designers,
            "catalogs": catalogs,
            "sections": sections,
            "genders": genders,
            "total": total,
            "overall_total": overall_total,
        }
