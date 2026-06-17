from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import Product, ProductListing, ProductListingGalleryImage, ProductListingMember, ProductPresentation
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
    def _resolved_weight_grams(product: Product, listing: ProductListing | None) -> int | None:
        if product.manual_weight_grams is not None and int(product.manual_weight_grams) > 0:
            return int(product.manual_weight_grams)
        if listing is not None and listing.source_weight_grams is not None and int(listing.source_weight_grams) > 0:
            return int(listing.source_weight_grams)
        if product.weight_rule is not None and product.weight_rule.weight_grams is not None:
            return int(product.weight_rule.weight_grams)
        return None

    @staticmethod
    def _gallery_urls(product: Product, listing: ProductListing | None) -> tuple[list[str], dict]:
        if listing is None:
            return [], {
                "hidden_source_image_urls": [],
                "manual_image_urls": [],
                "manual_image_order": [],
                "source_image_urls": [],
                "images_sync_locked": False,
            }
        scope = [
            row
            for row in sorted(product.gallery_images, key=lambda value: (int(value.position), int(value.id or 0)))
            if int(row.listing_id) == int(listing.id)
        ]
        source_urls = [image.url for image in sorted(listing.images, key=lambda value: int(value.position))]
        hidden_source_urls: list[str] = []
        manual_urls: list[str] = []
        ordered_urls: list[str] = []
        if scope:
            for row in scope:
                if row.image_asset_id is not None:
                    url = f"/api/v1/products/images/{int(row.image_asset_id)}"
                    manual_urls.append(url)
                    if not row.is_hidden:
                        ordered_urls.append(url)
                    continue
                source_url = str(getattr(row.listing_image, "url", "") or "").strip()
                if not source_url:
                    continue
                if row.is_hidden:
                    hidden_source_urls.append(source_url)
                    continue
                ordered_urls.append(source_url)
        else:
            ordered_urls = list(source_urls)
        return ordered_urls, {
            "hidden_source_image_urls": hidden_source_urls,
            "manual_image_urls": manual_urls,
            "manual_image_order": [*manual_urls],
            "source_image_urls": source_urls,
            "images_sync_locked": bool(hidden_source_urls or manual_urls),
        }

    @staticmethod
    def _effective_description(product: Product, listing: ProductListing | None) -> tuple[str | None, bool, bool | None]:
        description_visibility = None
        if product.presentation is not None:
            description_visibility = product.presentation.description_visibility
            if product.presentation.description_text:
                return str(product.presentation.description_text), bool(description_visibility is not False), description_visibility
            if product.presentation.description_html:
                return str(product.presentation.description_html), bool(description_visibility is not False), description_visibility

        if listing is None:
            return None, True, description_visibility
        description_mode = str(getattr(getattr(listing.source, "setting", None), "description_mode", "text") or "text").strip().lower()
        if description_mode == "hidden":
            return None, False, description_visibility
        if description_mode == "html":
            return listing.source_description_html or listing.source_description_text, True, description_visibility
        return listing.source_description_text or listing.source_description_html, True, description_visibility

    @staticmethod
    def _effective_title(product: Product, listing: ProductListing | None) -> str:
        if product.presentation is not None and product.presentation.title_override:
            return str(product.presentation.title_override)
        if listing is not None and listing.source_title:
            return str(listing.source_title)
        return f"Product {int(product.id)}"

    @staticmethod
    def _effective_vendor(product: Product, listing: ProductListing | None) -> str | None:
        if product.designer is not None and product.designer.name:
            return str(product.designer.name)
        if listing is not None and listing.source_designer_raw:
            return str(listing.source_designer_raw)
        return None

    @staticmethod
    def _effective_product_type(listing: ProductListing | None) -> str | None:
        if listing is None:
            return None
        return str(listing.source_category_raw) if listing.source_category_raw else None

    @staticmethod
    def _product_status(product: Product) -> str:
        if str(product.visibility_status or "").strip().lower() == "hidden":
            return "hidden"
        orderabilities = {
            str(membership.listing.orderability_status or "").strip().lower()
            for membership in product.memberships
            if membership.listing is not None
        }
        if "orderable" in orderabilities:
            return "available"
        if "sold_out" in orderabilities:
            return "out_of_stock"
        return "unavailable"

    @staticmethod
    def _decimal_to_float(value: Decimal | None) -> float | None:
        return float(value) if value is not None else None

    def _build_variants(self, product: Product) -> list[dict]:
        variants: list[dict] = []
        memberships = sorted(product.memberships, key=lambda item: int(item.listing_id))
        for membership in memberships:
            listing = membership.listing
            if listing is None:
                continue
            for variant in sorted(listing.variants, key=lambda item: int(item.position)):
                variants.append(
                    {
                        "title": str(variant.title or ""),
                        "option1": None,
                        "option2": None,
                        "option3": None,
                        "available": bool(variant.is_orderable),
                        "price": self._decimal_to_float(variant.price_amount),
                        "inventory_quantity": 1 if bool(variant.is_orderable) else 0,
                        "sku": variant.sku,
                        "currency": str(variant.currency_code or "").upper(),
                        "compare_at_price": self._decimal_to_float(variant.compare_at_price_amount),
                        "source_id": int(listing.source_id),
                        "source_name": str(getattr(listing.source, "name", "") or "") or None,
                        "listing_id": int(listing.id),
                    }
                )
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
        except Exception as exc:
            return None, {"manual_required": True, "reason": f"pricing_error:{exc.__class__.__name__}"}

    def build_product_payload(self, product: Product) -> dict:
        listing = self._resolved_primary_listing(product)
        title = self._effective_title(product, listing)
        description, description_visible_effective, description_override = self._effective_description(product, listing)
        image_urls, image_meta = self._gallery_urls(product, listing)
        variants = self._build_variants(product)
        weight_grams = self._resolved_weight_grams(product, listing)
        final_price, pricing_components = self._compute_pricing(product, listing, variants, weight_grams)
        source_price = next((variant.get("price") for variant in variants if variant.get("price") is not None), None)
        source_currency = next((variant.get("currency") for variant in variants if variant.get("currency")), None)
        status = self._product_status(product)

        return {
            "id": int(product.id),
            "source_id": int(listing.source_id) if listing is not None else 0,
            "handle": str(listing.handle or "") if listing is not None else "",
            "title": title,
            "vendor": self._effective_vendor(product, listing),
            "product_type": self._effective_product_type(listing),
            "url": str(listing.url or "") if listing is not None else "",
            "price": final_price if final_price is not None else source_price,
            "currency": "RUB" if final_price is not None else str(source_currency or ""),
            "source_price": source_price,
            "source_currency": source_currency,
            "final_price": final_price,
            "final_currency": "RUB" if final_price is not None else None,
            "pricing_manual_required": bool((pricing_components or {}).get("manual_required", False)),
            "pricing_reason": (pricing_components or {}).get("reason"),
            "pricing_components": pricing_components,
            "status": status,
            "image_count": len(image_urls),
            "image_urls": image_urls,
            "variants": variants,
            "description": description if description_visible_effective else None,
            "source_name": str(getattr(getattr(listing, "source", None), "name", "") or "") or None,
            "weight_grams": weight_grams,
            "product_edit": {
                "title_sync_locked": bool(product.presentation and product.presentation.title_override),
                "description_sync_locked": bool(product.presentation and (product.presentation.description_text or product.presentation.description_html)),
                "description_visible_override": description_override,
                "description_visible_effective": description_visible_effective,
                "images_sync_locked": bool(image_meta["images_sync_locked"]),
                "title_override": getattr(product.presentation, "title_override", None) if product.presentation is not None else None,
                "description_override": getattr(product.presentation, "description_text", None) if product.presentation is not None else None,
                **image_meta,
            },
            "created_at": product.created_at.isoformat() if product.created_at else None,
            "updated_at": product.updated_at.isoformat() if product.updated_at else None,
        }

    def list_products(self, *, limit: int, offset: int) -> dict:
        items = self.products.list_products(limit=limit, offset=offset)
        return {
            "items": [self.build_product_payload(product) for product in items],
            "total": self.products.count_products(),
            "limit": int(limit),
            "offset": int(offset),
        }

    def get_product_payload(self, product_id: int) -> dict | None:
        product = self.products.get_product(product_id)
        if product is None:
            return None
        return self.build_product_payload(product)

    def search_products(self, *, query: str, limit: int, offset: int) -> dict:
        normalized_query = " ".join(str(query or "").strip().lower().split())
        base_query = (
            self.db.query(Product.id.label("product_id"))
            .join(ProductListingMember, ProductListingMember.product_id == Product.id)
            .join(ProductListing, ProductListing.id == ProductListingMember.listing_id)
            .outerjoin(ProductPresentation, ProductPresentation.product_id == Product.id)
            .filter(Product.lifecycle_status != "merged")
        )
        if normalized_query:
            pattern = f"%{normalized_query}%"
            base_query = base_query.filter(
                or_(
                    func.lower(ProductListing.source_title).like(pattern),
                    func.lower(ProductListing.source_designer_raw).like(pattern),
                    func.lower(ProductListing.handle).like(pattern),
                    func.lower(ProductPresentation.title_override).like(pattern),
                )
            )

        filtered_ids = base_query.distinct().subquery()
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
