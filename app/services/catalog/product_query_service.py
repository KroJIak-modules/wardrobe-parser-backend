from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import Product, ProductListing, ProductListingMember, ProductPresentation
from app.repositories.catalog_products import CatalogProductRepository
from app.services.settings.pricing_service import PricingSettingsService


class ProductQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = CatalogProductRepository(db)
        self.pricing = PricingSettingsService(db)

    @staticmethod
    def _resolved_primary_listing(product: Product) -> ProductListing | None:
        if product.primary_listing is not None:
            return product.primary_listing
        listings = [membership.listing for membership in product.memberships if membership.listing is not None]
        return listings[0] if listings else None

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
            return str(listing.source_title)
        return f"Product {int(product.id)}"

    @staticmethod
    def _listing_payload(product: Product, listing: ProductListing) -> dict:
        gallery = ProductQueryService._gallery_state(product, listing)
        return {
            "id": int(listing.id),
            "source_id": int(listing.source_id),
            "source_name": str(getattr(getattr(listing, "source", None), "name", "") or "") or None,
            "ingest_mode": str(listing.ingest_mode),
            "external_id": str(listing.external_id) if listing.external_id else None,
            "url": str(listing.url),
            "handle": str(listing.handle) if listing.handle else None,
            "source_title": str(listing.source_title),
            "source_description_text": str(listing.source_description_text) if listing.source_description_text else None,
            "source_description_html": str(listing.source_description_html) if listing.source_description_html else None,
            "source_weight_grams": int(listing.source_weight_grams) if listing.source_weight_grams is not None else None,
            "source_designer_name": str(listing.source_designer_raw) if listing.source_designer_raw else None,
            "source_category_name": str(listing.source_category_raw) if listing.source_category_raw else None,
            "orderability_status": str(listing.orderability_status),
            "status_reason": str(listing.status_reason) if listing.status_reason else None,
            "image_urls": gallery["display_image_urls"],
            "gallery": gallery,
            "variants": [
                {
                    "id": int(variant.id),
                    "position": int(variant.position),
                    "title": str(variant.title),
                    "available": bool(variant.is_orderable),
                    "price": ProductQueryService._decimal_to_float(variant.price_amount),
                    "compare_at_price": ProductQueryService._decimal_to_float(variant.compare_at_price_amount),
                    "currency": str(variant.currency_code or "").upper() or None,
                    "sku": str(variant.sku) if variant.sku else None,
                    "source_ref_id": str(variant.source_ref_id) if variant.source_ref_id else None,
                }
                for variant in sorted(listing.variants, key=lambda item: int(item.position))
            ],
        }

    def _listing_variants(self, listing: ProductListing | None) -> list[dict]:
        if listing is None:
            return []
        return [
            {
                "id": int(variant.id),
                "title": str(variant.title or ""),
                "available": bool(variant.is_orderable),
                "price": self._decimal_to_float(variant.price_amount),
                "inventory_quantity": 1 if bool(variant.is_orderable) else 0,
                "sku": variant.sku,
                "currency": str(variant.currency_code or "").upper() or None,
                "compare_at_price": self._decimal_to_float(variant.compare_at_price_amount),
                "source_id": int(listing.source_id),
                "source_name": str(getattr(listing.source, "name", "") or "") or None,
                "listing_id": int(listing.id),
                "source_ref_id": str(variant.source_ref_id) if variant.source_ref_id else None,
            }
            for variant in sorted(listing.variants, key=lambda item: int(item.position))
        ]

    def _build_variants(self, product: Product) -> list[dict]:
        variants: list[dict] = []
        for membership in sorted(product.memberships, key=lambda item: int(item.listing_id)):
            listing = membership.listing
            if listing is None:
                continue
            variants.extend(self._listing_variants(listing))
        return variants

    def _compute_pricing(self, product: Product, listing: ProductListing | None, variants: list[dict], weight_grams: int | None) -> tuple[float | None, dict | None]:
        if listing is None:
            return None, None
        price_variant = next((variant for variant in variants if variant.get("price") is not None), None)
        source_price = float(price_variant["price"]) if price_variant and price_variant.get("price") is not None else None
        source_currency = str(price_variant.get("currency") or "").upper() or None if price_variant else None
        supplier_id = getattr(getattr(listing.source, "setting", None), "supplier_id", None)
        promo_factor = getattr(getattr(listing.source, "setting", None), "promo_factor", None)
        promo_only_no_discount = getattr(getattr(listing.source, "setting", None), "promo_only_no_discount", None)
        buyout_surcharge_value = getattr(getattr(listing.source, "setting", None), "buyout_surcharge_value", None)
        buyout_surcharge_currency = getattr(getattr(listing.source, "setting", None), "buyout_surcharge_currency", None)

        if product.price_override is not None and product.price_override.manual_price_rub is not None:
            final_price = float(product.price_override.manual_price_rub)
            return final_price, {
                "reason": None,
                "manual_override": True,
                "manual_compare_at_price_rub": (
                    float(product.price_override.manual_compare_at_price_rub)
                    if product.price_override.manual_compare_at_price_rub is not None
                    else None
                ),
            }

        try:
            settings = self.pricing.get_settings(refresh_bybit=False)
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

    def build_product_payload(self, product: Product) -> dict:
        primary_listing = self._resolved_primary_listing(product)
        title = self._effective_title(product, primary_listing)
        description = self._description_state(product, primary_listing)
        gallery = self._gallery_state(product, primary_listing)
        variants = self._build_variants(product)
        primary_listing_variants = self._listing_variants(primary_listing)
        effective_weight_grams = self._effective_weight_grams(product, primary_listing)
        final_price, pricing_components = self._compute_pricing(product, primary_listing, primary_listing_variants, effective_weight_grams)
        source_price = next((variant.get("price") for variant in primary_listing_variants if variant.get("price") is not None), None)
        source_currency = next((variant.get("currency") for variant in primary_listing_variants if variant.get("currency")), None)
        listings = [
            self._listing_payload(product, listing)
            for listing in self.products.list_product_listings(int(product.id))
        ]

        return {
            "id": int(product.id),
            "designer_id": int(product.designer_id) if product.designer_id is not None else None,
            "designer_name": str(getattr(product.designer, "name", "") or "") or None,
            "primary_listing_id": int(primary_listing.id) if primary_listing is not None else None,
            "gender": str(product.gender),
            "availability_mode": str(product.availability_mode),
            "visibility_status": str(product.visibility_status),
            "lifecycle_status": str(product.lifecycle_status),
            "manual_weight_grams": int(product.manual_weight_grams) if product.manual_weight_grams is not None else None,
            "weight_rule_id": int(product.weight_rule_id) if product.weight_rule_id is not None else None,
            "effective_weight_grams": effective_weight_grams,
            "title": title,
            "url": str(primary_listing.url) if primary_listing is not None else "",
            "handle": str(primary_listing.handle) if primary_listing is not None and primary_listing.handle else None,
            "source_id": int(primary_listing.source_id) if primary_listing is not None else None,
            "source_name": str(getattr(getattr(primary_listing, "source", None), "name", "") or "") or None,
            "source_category_name": str(primary_listing.source_category_raw) if primary_listing is not None and primary_listing.source_category_raw else None,
            "source_designer_name": str(primary_listing.source_designer_raw) if primary_listing is not None and primary_listing.source_designer_raw else None,
            "orderability_status": str(primary_listing.orderability_status) if primary_listing is not None else "unavailable",
            "status_reason": str(primary_listing.status_reason) if primary_listing is not None and primary_listing.status_reason else None,
            "description_mode": description["description_mode"],
            "description": description["public_description"],
            "description_text": description["effective_text"],
            "description_html": description["effective_html"],
            "description_public_visible": description["public_visible"],
            "price": final_price if final_price is not None else source_price,
            "currency": "RUB" if final_price is not None else source_currency,
            "source_price": source_price,
            "source_currency": source_currency,
            "final_price": final_price,
            "final_currency": "RUB" if final_price is not None else None,
            "pricing_components": pricing_components,
            "price_override": (
                {
                    "manual_price_rub": float(product.price_override.manual_price_rub),
                    "manual_compare_at_price_rub": (
                        float(product.price_override.manual_compare_at_price_rub)
                        if product.price_override.manual_compare_at_price_rub is not None
                        else None
                    ),
                }
                if product.price_override is not None and product.price_override.manual_price_rub is not None
                else None
            ),
            "image_urls": gallery["display_image_urls"],
            "gallery": gallery,
            "variants": variants,
            "presentation": {
                "title_override": getattr(product.presentation, "title_override", None) if product.presentation is not None else None,
                "description_text": getattr(product.presentation, "description_text", None) if product.presentation is not None else None,
                "description_html": getattr(product.presentation, "description_html", None) if product.presentation is not None else None,
                "description_visibility": getattr(product.presentation, "description_visibility", None) if product.presentation is not None else None,
            },
            "listings": listings,
            "created_at": product.created_at.isoformat() if product.created_at else None,
            "updated_at": product.updated_at.isoformat() if product.updated_at else None,
        }

    def _base_product_id_query(
        self,
        *,
        query: str = "",
        source_id: int | None = None,
        designer_id: int | None = None,
        visibility_status: str | None = None,
        availability_mode: str | None = None,
        orderability_status: str | None = None,
    ):
        base_query = (
            self.db.query(Product.id.label("product_id"))
            .join(ProductListingMember, ProductListingMember.product_id == Product.id)
            .join(ProductListing, ProductListing.id == ProductListingMember.listing_id)
            .outerjoin(ProductPresentation, ProductPresentation.product_id == Product.id)
            .filter(Product.lifecycle_status != "merged")
        )
        if source_id is not None:
            base_query = base_query.filter(ProductListing.source_id == int(source_id))
        if designer_id is not None:
            base_query = base_query.filter(Product.designer_id == int(designer_id))
        if visibility_status:
            base_query = base_query.filter(Product.visibility_status == str(visibility_status).strip().lower())
        if availability_mode:
            base_query = base_query.filter(Product.availability_mode == str(availability_mode).strip().lower())
        if orderability_status:
            base_query = base_query.filter(ProductListing.orderability_status == str(orderability_status).strip().lower())

        normalized_query = " ".join(str(query or "").strip().lower().split())
        if normalized_query:
            pattern = f"%{normalized_query}%"
            base_query = base_query.filter(
                or_(
                    func.lower(func.coalesce(ProductListing.source_title, "")).like(pattern),
                    func.lower(func.coalesce(ProductListing.source_description_text, "")).like(pattern),
                    func.lower(func.coalesce(ProductListing.source_description_html, "")).like(pattern),
                    func.lower(func.coalesce(ProductListing.source_designer_raw, "")).like(pattern),
                    func.lower(func.coalesce(ProductListing.source_category_raw, "")).like(pattern),
                    func.lower(func.coalesce(ProductListing.handle, "")).like(pattern),
                    func.lower(func.coalesce(ProductListing.url, "")).like(pattern),
                    func.lower(func.coalesce(ProductPresentation.title_override, "")).like(pattern),
                    func.lower(func.coalesce(ProductPresentation.description_text, "")).like(pattern),
                    func.lower(func.coalesce(ProductPresentation.description_html, "")).like(pattern),
                )
            )
        return base_query

    def list_products(
        self,
        *,
        limit: int,
        offset: int,
        query: str = "",
        source_id: int | None = None,
        designer_id: int | None = None,
        visibility_status: str | None = None,
        availability_mode: str | None = None,
        orderability_status: str | None = None,
    ) -> dict:
        filtered_ids = self._base_product_id_query(
            query=query,
            source_id=source_id,
            designer_id=designer_id,
            visibility_status=visibility_status,
            availability_mode=availability_mode,
            orderability_status=orderability_status,
        ).distinct().subquery()
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
        items = [
            payload
            for product_id in product_ids
            if (payload := self.get_product_payload(product_id)) is not None
        ]
        return {
            "items": items,
            "total": total,
            "limit": int(limit),
            "offset": int(offset),
        }

    def get_product_payload(self, product_id: int) -> dict | None:
        product = self.products.get_product(product_id)
        if product is None:
            return None
        return self.build_product_payload(product)

    def search_products(self, *, query: str, limit: int, offset: int) -> dict:
        return self.list_products(limit=limit, offset=offset, query=query)
