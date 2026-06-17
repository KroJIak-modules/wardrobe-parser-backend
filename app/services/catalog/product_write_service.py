from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.models import ImageAsset, ProductListingGalleryImage
from app.repositories.catalog_products import CatalogProductRepository
from app.services.catalog.source_registry_service import SourceRegistryService


class ProductWriteService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = CatalogProductRepository(db)
        self.sources = SourceRegistryService(db)

    @staticmethod
    def _normalize_status(raw: str | None) -> str:
        value = str(raw or "").strip().lower()
        if value in {"available", "out_of_stock", "hidden"}:
            return value
        return "available"

    def _product_or_error(self, product_id: int):
        product = self.products.get_product(product_id)
        if product is None:
            raise NotFoundError("Товар не найден")
        return product

    def _primary_listing_or_error(self, product):
        listing = product.primary_listing or (product.memberships[0].listing if product.memberships else None)
        if listing is None:
            raise ValidationError("У товара нет основного listing")
        return listing

    def _gallery_asset_ids_from_urls(self, manual_image_urls: list[str]) -> list[int]:
        asset_ids: list[int] = []
        for url in manual_image_urls:
            value = str(url or "").strip()
            if not value.startswith("/api/v1/products/images/"):
                continue
            tail = value.rsplit("/", 1)[-1]
            try:
                asset_id = int(tail)
            except Exception:
                continue
            asset_ids.append(asset_id)
        return asset_ids

    def _replace_gallery_scope(
        self,
        *,
        product_id: int,
        listing_id: int,
        hidden_source_image_urls: list[str],
        manual_image_urls: list[str],
        manual_image_order: list[str],
    ) -> None:
        scope_rows = self.products.list_gallery_scope(product_id=product_id, listing_id=listing_id)
        for row in scope_rows:
            self.db.delete(row)
        self.db.flush()

        listing = self.products.get_listing(listing_id)
        if listing is None:
            return
        source_images = {str(image.url): image for image in listing.images}
        manual_asset_ids = self._gallery_asset_ids_from_urls(manual_image_urls)
        manual_assets = {
            int(asset.id): asset
            for asset in self.db.query(ImageAsset).filter(ImageAsset.id.in_(manual_asset_ids)).all()
        } if manual_asset_ids else {}

        ordered_manual_urls = [url for url in manual_image_order if url in manual_image_urls]
        for url in manual_image_urls:
            if url not in ordered_manual_urls:
                ordered_manual_urls.append(url)

        position = 1
        visible_source_urls = [url for url in source_images.keys() if url not in set(hidden_source_image_urls)]
        for source_url in visible_source_urls:
            image = source_images[source_url]
            self.db.add(
                ProductListingGalleryImage(
                    product_id=int(product_id),
                    listing_id=int(listing_id),
                    listing_image_id=int(image.id),
                    position=position,
                    is_hidden=False,
                    origin_kind="source",
                )
            )
            position += 1

        for manual_url in ordered_manual_urls:
            asset_id = self._gallery_asset_ids_from_urls([manual_url])
            if not asset_id:
                continue
            if asset_id[0] not in manual_assets:
                continue
            self.db.add(
                ProductListingGalleryImage(
                    product_id=int(product_id),
                    listing_id=int(listing_id),
                    image_asset_id=int(asset_id[0]),
                    position=position,
                    is_hidden=False,
                    origin_kind="uploaded",
                )
            )
            position += 1

        for hidden_url in hidden_source_image_urls:
            image = source_images.get(hidden_url)
            if image is None:
                continue
            self.db.add(
                ProductListingGalleryImage(
                    product_id=int(product_id),
                    listing_id=int(listing_id),
                    listing_image_id=int(image.id),
                    position=position,
                    is_hidden=True,
                    origin_kind="source",
                )
            )
            position += 1

        self.db.flush()

    def update_product(self, *, product_id: int, payload: dict) -> None:
        product = self._product_or_error(product_id)
        listing = self._primary_listing_or_error(product)
        presentation = self.products.ensure_presentation(int(product.id))

        reset_to_default = {str(item).strip() for item in payload.get("reset_to_default") or []}
        if "title" in reset_to_default:
            presentation.title_override = None
        elif "title" in payload:
            presentation.title_override = str(payload.get("title") or "").strip() or None

        if "description" in reset_to_default:
            presentation.description_text = None
            presentation.description_html = None
        elif "description" in payload:
            presentation.description_text = str(payload.get("description") or "").strip() or None
            presentation.description_html = None

        if "description_visibility" in reset_to_default:
            presentation.description_visibility = None
        elif "description_visible" in payload:
            presentation.description_visibility = bool(payload.get("description_visible"))

        if "status" in payload:
            status_value = self._normalize_status(payload.get("status"))
            if status_value == "hidden":
                product.visibility_status = "hidden"
            else:
                product.visibility_status = "visible"
                listing.orderability_status = "orderable" if status_value == "available" else "sold_out"
                listing.status_reason = None

        images_patch = payload.get("images") if isinstance(payload.get("images"), dict) else None
        if "images" in reset_to_default:
            self.products.replace_gallery_scope_with_source_images(
                product_id=int(product.id),
                listing_id=int(listing.id),
                listing_images=listing.images,
            )
        elif images_patch is not None:
            hidden_source_image_urls = [str(url).strip() for url in images_patch.get("hidden_source_image_urls") or [] if str(url).strip()]
            manual_image_urls = [str(url).strip() for url in images_patch.get("manual_image_urls") or [] if str(url).strip()]
            manual_image_order = [str(url).strip() for url in images_patch.get("manual_image_order") or [] if str(url).strip()]
            self._replace_gallery_scope(
                product_id=int(product.id),
                listing_id=int(listing.id),
                hidden_source_image_urls=hidden_source_image_urls,
                manual_image_urls=manual_image_urls,
                manual_image_order=manual_image_order,
            )

        self.db.flush()

    def create_manual_product(self, payload: dict) -> int:
        source = self.sources.ensure_manual_source()
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValidationError("title is required")

        product = self.products.create_product(
            gender="unisex",
            availability_mode="in_stock",
            lifecycle_status="active",
            visibility_status="hidden" if self._normalize_status(payload.get("status")) == "hidden" else "visible",
            manual_weight_grams=(int(payload["weight_grams"]) if payload.get("weight_grams") is not None else None),
        )
        listing = self.products.create_listing(
            source_id=int(source.id),
            external_id=None,
            url=f"manual://product/{product.id}",
            handle=f"manual-{product.id}",
            source_title=title,
            source_description_text=str(payload.get("description") or "").strip() or None,
            source_description_html=None,
            source_weight_grams=None,
            source_designer_raw=str(payload.get("vendor") or "").strip() or None,
            source_category_raw=str(payload.get("product_type") or "").strip() or None,
            orderability_status="sold_out" if self._normalize_status(payload.get("status")) == "out_of_stock" else "orderable",
            status_reason=None,
            ingest_mode="manual",
        )
        self.products.ensure_membership(product_id=int(product.id), listing_id=int(listing.id))
        product.primary_listing_id = int(listing.id)

        variants = []
        for item in payload.get("variants") or []:
            if not isinstance(item, dict):
                continue
            variants.append(
                {
                    "title": str(item.get("title") or "").strip() or "Default",
                    "price_amount": Decimal(str(item.get("price"))) if item.get("price") is not None else None,
                    "compare_at_price_amount": None,
                    "currency_code": str(item.get("currency") or "").strip().upper() or None,
                    "is_orderable": bool(item.get("available", True)),
                    "source_ref_id": None,
                    "sku": None,
                }
            )
        self.products.replace_variants(listing_id=int(listing.id), variants=variants)

        manual_image_ids = [int(value) for value in payload.get("manual_image_asset_ids") or [] if int(value) > 0]
        manual_urls = [f"/api/v1/products/images/{image_id}" for image_id in manual_image_ids]
        self._replace_gallery_scope(
            product_id=int(product.id),
            listing_id=int(listing.id),
            hidden_source_image_urls=[],
            manual_image_urls=manual_urls,
            manual_image_order=manual_urls,
        )

        self.db.flush()
        return int(product.id)

    def update_manual_product(self, *, product_id: int, payload: dict) -> None:
        product = self._product_or_error(product_id)
        listing = self._primary_listing_or_error(product)
        if str(listing.ingest_mode or "") != "manual":
            raise ValidationError("Только manual listing можно редактировать этим методом")

        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValidationError("title is required")
        listing.source_title = title
        listing.source_description_text = str(payload.get("description") or "").strip() or None
        listing.source_designer_raw = str(payload.get("vendor") or "").strip() or None
        listing.source_category_raw = str(payload.get("product_type") or "").strip() or None
        product.manual_weight_grams = int(payload["weight_grams"]) if payload.get("weight_grams") is not None else None
        status_value = self._normalize_status(payload.get("status"))
        product.visibility_status = "hidden" if status_value == "hidden" else "visible"
        listing.orderability_status = "sold_out" if status_value == "out_of_stock" else "orderable"
        listing.status_reason = None

        variants = []
        for item in payload.get("variants") or []:
            if not isinstance(item, dict):
                continue
            variants.append(
                {
                    "title": str(item.get("title") or "").strip() or "Default",
                    "price_amount": Decimal(str(item.get("price"))) if item.get("price") is not None else None,
                    "compare_at_price_amount": None,
                    "currency_code": str(item.get("currency") or "").strip().upper() or None,
                    "is_orderable": bool(item.get("available", True)),
                    "source_ref_id": None,
                    "sku": None,
                }
            )
        self.products.replace_variants(listing_id=int(listing.id), variants=variants)

        manual_image_ids = [int(value) for value in payload.get("manual_image_asset_ids") or [] if int(value) > 0]
        manual_urls = [f"/api/v1/products/images/{image_id}" for image_id in manual_image_ids]
        self._replace_gallery_scope(
            product_id=int(product.id),
            listing_id=int(listing.id),
            hidden_source_image_urls=[],
            manual_image_urls=manual_urls,
            manual_image_order=manual_urls,
        )
        self.db.flush()

    def delete_manual_product(self, *, product_id: int) -> None:
        product = self._product_or_error(product_id)
        listings = self.products.list_product_listings(int(product.id))
        if not listings:
            self.db.delete(product)
            self.db.flush()
            return
        if any(str(listing.ingest_mode or "") != "manual" for listing in listings):
            product.visibility_status = "hidden"
            self.db.flush()
            return
        for listing in listings:
            self.db.delete(listing)
        self.db.delete(product)
        self.db.flush()
