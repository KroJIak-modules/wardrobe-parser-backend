from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.models import (
    CustomCatalog,
    CustomCatalogProduct,
    Filter,
    FilterManualProduct,
    ImageAsset,
    ProductListingGalleryImage,
    ProductListingMember,
)
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
    def _manual_listing(product):
        for membership in product.memberships:
            listing = membership.listing
            if listing is not None and str(listing.ingest_mode or "") == "manual":
                return listing
        return None

    def _manual_listing_or_error(self, product):
        listing = self._manual_listing(product)
        if listing is None:
            raise ValidationError("У товара нет manual listing")
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

    def _listing_for_gallery_or_error(self, *, product, listing_id: int | None) -> object:
        target_listing_id = int(listing_id) if listing_id is not None else None
        if target_listing_id is None:
            return self._primary_listing_or_error(product)
        if not self._listing_belongs_to_product(product_id=int(product.id), listing_id=target_listing_id):
            raise ValidationError("gallery listing не принадлежит товару")
        listing = self.products.get_listing(target_listing_id)
        if listing is None:
            raise ValidationError("gallery listing не найден")
        return listing

    @staticmethod
    def _normalize_slug_list(values: object) -> list[str]:
        if not isinstance(values, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for raw_value in values:
            value = str(raw_value or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _replace_taxonomy_links(
        self,
        *,
        product_id: int,
        filter_slugs: object | None = None,
        custom_catalog_slugs: object | None = None,
    ) -> None:
        if filter_slugs is not None:
            normalized_filter_slugs = self._normalize_slug_list(filter_slugs)
            filters = (
                self.db.query(Filter)
                .filter(Filter.slug.in_(normalized_filter_slugs))
                .order_by(Filter.slug.asc(), Filter.id.asc())
                .all()
            ) if normalized_filter_slugs else []
            found_slugs = {str(entity.slug) for entity in filters}
            missing = [slug for slug in normalized_filter_slugs if slug not in found_slugs]
            if missing:
                raise ValidationError(f"Не найдены filters: {', '.join(missing)}")
            (
                self.db.query(FilterManualProduct)
                .filter(FilterManualProduct.product_id == int(product_id))
                .delete(synchronize_session=False)
            )
            for entity in filters:
                self.db.add(FilterManualProduct(filter_id=int(entity.id), product_id=int(product_id)))

        if custom_catalog_slugs is not None:
            normalized_catalog_slugs = self._normalize_slug_list(custom_catalog_slugs)
            catalogs = (
                self.db.query(CustomCatalog)
                .filter(CustomCatalog.slug.in_(normalized_catalog_slugs))
                .order_by(CustomCatalog.slug.asc(), CustomCatalog.id.asc())
                .all()
            ) if normalized_catalog_slugs else []
            found_slugs = {str(entity.slug) for entity in catalogs}
            missing = [slug for slug in normalized_catalog_slugs if slug not in found_slugs]
            if missing:
                raise ValidationError(f"Не найдены custom catalogs: {', '.join(missing)}")
            (
                self.db.query(CustomCatalogProduct)
                .filter(CustomCatalogProduct.product_id == int(product_id))
                .delete(synchronize_session=False)
            )
            for entity in catalogs:
                self.db.add(CustomCatalogProduct(catalog_id=int(entity.id), product_id=int(product_id)))

        self.db.flush()

    def _duplicate_gallery_scope(self, *, from_product_id: int, to_product_id: int, listing_id: int) -> None:
        scope_rows = self.products.list_gallery_scope(product_id=from_product_id, listing_id=listing_id)
        if not scope_rows:
            listing = self.products.get_listing(listing_id)
            if listing is not None:
                self.products.replace_gallery_scope_with_source_images(
                    product_id=to_product_id,
                    listing_id=listing_id,
                    listing_images=listing.images,
                )
            return
        for row in scope_rows:
            self.db.add(
                ProductListingGalleryImage(
                    product_id=int(to_product_id),
                    listing_id=int(listing_id),
                    listing_image_id=(int(row.listing_image_id) if row.listing_image_id is not None else None),
                    image_asset_id=(int(row.image_asset_id) if row.image_asset_id is not None else None),
                    position=int(row.position),
                    is_hidden=bool(row.is_hidden),
                    origin_kind=str(row.origin_kind),
                )
            )
        self.db.flush()

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
        manual_listing = self._manual_listing(product)

        reset_to_default = {str(item).strip() for item in payload.get("reset_to_default") or []}
        if manual_listing is not None:
            if "title_override" in payload:
                next_title = str(payload.get("title_override") or "").strip()
                if not next_title:
                    raise ValidationError("manual title is required")
                manual_listing.source_title = next_title

            if "description_text" in reset_to_default:
                manual_listing.source_description_text = None
            elif "description_text" in payload:
                manual_listing.source_description_text = str(payload.get("description_text") or "").strip() or None

            if "description_html" in reset_to_default:
                manual_listing.source_description_html = None
            elif "description_html" in payload:
                manual_listing.source_description_html = str(payload.get("description_html") or "").strip() or None
        else:
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

        if "filter_slugs" in payload or "custom_catalog_slugs" in payload:
            self._replace_taxonomy_links(
                product_id=int(product.id),
                filter_slugs=payload.get("filter_slugs") if "filter_slugs" in payload else None,
                custom_catalog_slugs=payload.get("custom_catalog_slugs") if "custom_catalog_slugs" in payload else None,
            )

        images_patch = payload.get("images") if isinstance(payload.get("images"), dict) else None
        gallery_listing = self._listing_for_gallery_or_error(product=product, listing_id=self._int_or_none(payload.get("gallery_listing_id")))
        if "images" in reset_to_default:
            self.products.replace_gallery_scope_with_source_images(
                product_id=int(product.id),
                listing_id=int(gallery_listing.id),
                listing_images=gallery_listing.images,
            )
        elif images_patch is not None:
            hidden_source_image_urls = [str(url).strip() for url in images_patch.get("hidden_source_image_urls") or [] if str(url).strip()]
            manual_image_urls = [str(url).strip() for url in images_patch.get("manual_image_urls") or [] if str(url).strip()]
            manual_image_order = [str(url).strip() for url in images_patch.get("manual_image_order") or [] if str(url).strip()]
            self._replace_gallery_scope(
                product_id=int(product.id),
                listing_id=int(gallery_listing.id),
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
        self._replace_taxonomy_links(
            product_id=int(product.id),
            filter_slugs=payload.get("filter_slugs"),
            custom_catalog_slugs=payload.get("custom_catalog_slugs"),
        )
        self._sync_weight_state(product=product, listing=listing)
        self.db.flush()
        return int(product.id)

    def update_manual_product(self, *, product_id: int, payload: dict) -> None:
        product = self._product_or_error(product_id)
        listing = self._manual_listing_or_error(product)

        if "title" in payload:
            title = str(payload.get("title") or "").strip()
            if not title:
                raise ValidationError("title is required")
            listing.source_title = title
        if "designer_id" in payload:
            product.designer_id = self._int_or_none(payload.get("designer_id"))
        if "gender" in payload:
            product.gender = self._normalize_gender(payload.get("gender"))
        if "availability_mode" in payload:
            product.availability_mode = self._normalize_availability_mode(payload.get("availability_mode"))
        if "visibility_status" in payload:
            product.visibility_status = self._normalize_visibility_status(payload.get("visibility_status"))
        if "description_text" in payload:
            listing.source_description_text = str(payload.get("description_text") or "").strip() or None
        if "description_html" in payload:
            listing.source_description_html = str(payload.get("description_html") or "").strip() or None
        if "designer_name" in payload:
            listing.source_designer_raw = str(payload.get("designer_name") or "").strip() or None
        if "source_category_name" in payload:
            listing.source_category_raw = str(payload.get("source_category_name") or "").strip() or None
        if "manual_weight_grams" in payload:
            product.manual_weight_grams = self._int_or_none(payload.get("manual_weight_grams"))
        if "orderability_status" in payload:
            listing.orderability_status = self._normalize_orderability_status(payload.get("orderability_status"))
            listing.status_reason = None

        if "variants" in payload:
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

        if "manual_image_asset_ids" in payload:
            manual_image_ids = [int(value) for value in payload.get("manual_image_asset_ids") or [] if int(value) > 0]
            manual_urls = [f"/api/v1/products/images/{image_id}" for image_id in manual_image_ids]
            gallery_listing = self._listing_for_gallery_or_error(product=product, listing_id=self._int_or_none(payload.get("gallery_listing_id")))
            self._replace_gallery_scope(
                product_id=int(product.id),
                listing_id=int(gallery_listing.id),
                hidden_source_image_urls=[],
                manual_image_urls=manual_urls,
                manual_image_order=manual_urls,
            )
        if "price_override" in payload:
            self._apply_price_override(
                product_id=int(product.id),
                payload=(payload.get("price_override") if isinstance(payload.get("price_override"), dict) else None),
                reset=False,
            )
        if "filter_slugs" in payload or "custom_catalog_slugs" in payload:
            self._replace_taxonomy_links(
                product_id=int(product.id),
                filter_slugs=payload.get("filter_slugs") if "filter_slugs" in payload else None,
                custom_catalog_slugs=payload.get("custom_catalog_slugs") if "custom_catalog_slugs" in payload else None,
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
        sync_listings = [listing for listing in listings if str(listing.ingest_mode or "") == "sync"]
        for listing in sync_listings:
            detached = self.products.create_product(
                designer_id=product.designer_id,
                gender=str(product.gender),
                availability_mode=str(product.availability_mode),
                lifecycle_status="active",
                visibility_status="visible",
                manual_weight_grams=None,
                weight_rule_id=None,
            )
            self.products.ensure_membership(product_id=int(detached.id), listing_id=int(listing.id))
            detached.primary_listing_id = int(listing.id)
            self._duplicate_gallery_scope(
                from_product_id=int(product.id),
                to_product_id=int(detached.id),
                listing_id=int(listing.id),
            )
            for row in self.products.list_gallery_scope(product_id=int(product.id), listing_id=int(listing.id)):
                self.db.delete(row)
        for listing in listings:
            if str(listing.ingest_mode or "") == "manual":
                self.db.delete(listing)
        self.db.delete(product)
        self.db.flush()

    def unbind_listing(self, *, product_id: int, listing_id: int) -> int:
        product = self._product_or_error(product_id)
        listing = self.products.get_listing(listing_id)
        if listing is None:
            raise NotFoundError("Листинг не найден")
        if not self._listing_belongs_to_product(product_id=int(product.id), listing_id=int(listing.id)):
            raise ValidationError("listing не принадлежит товару")
        if str(listing.ingest_mode or "") != "sync":
            raise ValidationError("Можно отвязать только sync listing")

        detached = self.products.create_product(
            designer_id=product.designer_id,
            gender=str(product.gender),
            availability_mode=str(product.availability_mode),
            lifecycle_status="active",
            visibility_status="visible",
            manual_weight_grams=None,
            weight_rule_id=None,
        )
        self.products.ensure_membership(product_id=int(detached.id), listing_id=int(listing.id))
        detached.primary_listing_id = int(listing.id)
        self._duplicate_gallery_scope(
            from_product_id=int(product.id),
            to_product_id=int(detached.id),
            listing_id=int(listing.id),
        )
        for row in self.products.list_gallery_scope(product_id=int(product.id), listing_id=int(listing.id)):
            self.db.delete(row)

        remaining_memberships = (
            self.db.query(ProductListingMember)
            .filter(ProductListingMember.product_id == int(product.id))
            .order_by(ProductListingMember.listing_id.asc())
            .all()
        )
        if not remaining_memberships:
            self.db.delete(product)
            self.db.flush()
            return int(detached.id)

        if int(product.primary_listing_id or 0) == int(listing.id):
            product.primary_listing_id = int(remaining_memberships[0].listing_id)
        self.db.flush()
        return int(detached.id)
