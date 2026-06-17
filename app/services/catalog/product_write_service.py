from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.models import ImageAsset, ProductListingGalleryImage
from app.repositories.catalog_products import CatalogProductRepository
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.source_registry_service import SourceRegistryService


class ProductWriteService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = CatalogProductRepository(db)
        self.sources = SourceRegistryService(db)

    @staticmethod
    def _normalize_visibility_status(raw: str | None) -> str:
        value = str(raw or "").strip().lower()
        if value in {"visible", "hidden"}:
            return value
        return "visible"

    @staticmethod
    def _normalize_availability_mode(raw: str | None) -> str:
        value = str(raw or "").strip().lower()
        if value in {"in_stock", "by_order"}:
            return value
        return "by_order"

    @staticmethod
    def _normalize_gender(raw: str | None) -> str:
        value = str(raw or "").strip().lower()
        if value in {"male", "female", "unisex"}:
            return value
        return "unisex"

    @staticmethod
    def _normalize_orderability_status(raw: str | None) -> str:
        value = str(raw or "").strip().lower()
        if value in {"orderable", "sold_out", "unavailable"}:
            return value
        return "orderable"

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

    @staticmethod
    def _int_or_none(value: object) -> int | None:
        try:
            if value is None or str(value).strip() == "":
                return None
            candidate = int(value)
        except Exception:
            return None
        return candidate if candidate > 0 else None

    def _listing_belongs_to_product(self, *, product_id: int, listing_id: int) -> bool:
        return any(int(listing.id) == int(listing_id) for listing in self.products.list_product_listings(int(product_id)))

    def _sync_weight_state(self, *, product, listing) -> None:
        incoming_status = self._normalize_orderability_status(listing.orderability_status)
        incoming_reason = str(listing.status_reason or "").strip().lower() or None
        if incoming_reason == "missing_weight":
            variant_states = [bool(variant.is_orderable) for variant in getattr(listing, "variants", [])]
            if variant_states:
                incoming_status = "orderable" if any(variant_states) else "sold_out"
            incoming_reason = None
        listing.orderability_status, listing.status_reason = ProductIngestService(self.db)._resolve_listing_status(
            product=product,
            listing=listing,
            incoming_status=incoming_status,
            incoming_reason=incoming_reason,
            incoming_reasons=([incoming_reason] if incoming_reason else []),
        )

    def _apply_price_override(self, *, product_id: int, payload: dict | None, reset: bool = False) -> None:
        if reset or payload is None:
            self.products.delete_price_override(int(product_id))
            return
        manual_price_rub = payload.get("manual_price_rub")
        if manual_price_rub is None:
            self.products.delete_price_override(int(product_id))
            return
        self.products.upsert_price_override(
            product_id=int(product_id),
            manual_price_rub=float(manual_price_rub),
            manual_compare_at_price_rub=(
                float(payload["manual_compare_at_price_rub"])
                if payload.get("manual_compare_at_price_rub") is not None
                else None
            ),
        )

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
                    origin_kind="source_image",
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
                    origin_kind="uploaded_asset",
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
                    origin_kind="source_image",
                )
            )
            position += 1

        self.db.flush()

    def update_product(self, *, product_id: int, payload: dict) -> None:
        product = self._product_or_error(product_id)
        if payload.get("primary_listing_id") is not None:
            primary_listing_id = int(payload["primary_listing_id"])
            if not self._listing_belongs_to_product(product_id=int(product.id), listing_id=primary_listing_id):
                raise ValidationError("primary_listing_id не принадлежит товару")
            product.primary_listing_id = primary_listing_id
        listing = self._primary_listing_or_error(product)
        presentation = self.products.ensure_presentation(int(product.id))

        reset_to_default = {str(item).strip() for item in payload.get("reset_to_default") or []}
        if "title_override" in reset_to_default:
            presentation.title_override = None
        elif "title_override" in payload:
            presentation.title_override = str(payload.get("title_override") or "").strip() or None

        if "description_text" in reset_to_default:
            presentation.description_text = None
        elif "description_text" in payload:
            presentation.description_text = str(payload.get("description_text") or "").strip() or None

        if "description_html" in reset_to_default:
            presentation.description_html = None
        elif "description_html" in payload:
            presentation.description_html = str(payload.get("description_html") or "").strip() or None

        if "description_visibility" in reset_to_default:
            presentation.description_visibility = None
        elif "description_visibility" in payload:
            presentation.description_visibility = bool(payload.get("description_visibility"))

        if "visibility_status" in payload:
            product.visibility_status = self._normalize_visibility_status(payload.get("visibility_status"))

        if "availability_mode" in payload:
            product.availability_mode = self._normalize_availability_mode(payload.get("availability_mode"))

        if "gender" in payload:
            product.gender = self._normalize_gender(payload.get("gender"))

        if "manual_weight_grams" in reset_to_default:
            product.manual_weight_grams = None
        elif "manual_weight_grams" in payload:
            product.manual_weight_grams = self._int_or_none(payload.get("manual_weight_grams"))

        if "price_override" in reset_to_default:
            self._apply_price_override(product_id=int(product.id), payload=None, reset=True)
        elif "price_override" in payload:
            price_payload = payload.get("price_override") if isinstance(payload.get("price_override"), dict) else None
            self._apply_price_override(product_id=int(product.id), payload=price_payload, reset=False)

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

        self._sync_weight_state(product=product, listing=listing)
        self.db.flush()

    def create_manual_product(self, payload: dict) -> int:
        source = self.sources.ensure_manual_source()
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValidationError("title is required")

        product = self.products.create_product(
            designer_id=self._int_or_none(payload.get("designer_id")),
            gender=self._normalize_gender(payload.get("gender")),
            availability_mode=self._normalize_availability_mode(payload.get("availability_mode") or "in_stock"),
            lifecycle_status="active",
            visibility_status=self._normalize_visibility_status(payload.get("visibility_status")),
            manual_weight_grams=self._int_or_none(payload.get("manual_weight_grams")),
        )
        listing = self.products.create_listing(
            source_id=int(source.id),
            external_id=None,
            url=f"manual://product/{product.id}",
            handle=f"manual-{product.id}",
            source_title=title,
            source_description_text=str(payload.get("description_text") or "").strip() or None,
            source_description_html=str(payload.get("description_html") or "").strip() or None,
            source_weight_grams=None,
            source_designer_raw=str(payload.get("designer_name") or "").strip() or None,
            source_category_raw=str(payload.get("source_category_name") or "").strip() or None,
            orderability_status=self._normalize_orderability_status(payload.get("orderability_status")),
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

        self._apply_price_override(
            product_id=int(product.id),
            payload=(payload.get("price_override") if isinstance(payload.get("price_override"), dict) else None),
            reset=False,
        )
        self._sync_weight_state(product=product, listing=listing)
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
        product.designer_id = self._int_or_none(payload.get("designer_id"))
        product.gender = self._normalize_gender(payload.get("gender"))
        product.availability_mode = self._normalize_availability_mode(payload.get("availability_mode"))
        product.visibility_status = self._normalize_visibility_status(payload.get("visibility_status"))
        listing.source_title = title
        listing.source_description_text = str(payload.get("description_text") or "").strip() or None
        listing.source_description_html = str(payload.get("description_html") or "").strip() or None
        listing.source_designer_raw = str(payload.get("designer_name") or "").strip() or None
        listing.source_category_raw = str(payload.get("source_category_name") or "").strip() or None
        product.manual_weight_grams = self._int_or_none(payload.get("manual_weight_grams"))
        listing.orderability_status = self._normalize_orderability_status(payload.get("orderability_status"))
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
        self._apply_price_override(
            product_id=int(product.id),
            payload=(payload.get("price_override") if isinstance(payload.get("price_override"), dict) else None),
            reset=False,
        )
        self._sync_weight_state(product=product, listing=listing)
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
