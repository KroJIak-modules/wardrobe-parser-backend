from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.orm import Session, joinedload

from app.core.source_identity import normalize_host, normalize_listing_url
from app.models import (
    Product,
    ProductListing,
    ProductListingGalleryImage,
    ProductListingImage,
    ProductListingMember,
    ProductListingVariant,
    ProductPresentation,
    ProductPriceOverride,
)


class CatalogProductRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_products(self, *, limit: int, offset: int, include_merged: bool = False) -> list[Product]:
        query = (
            self.session.query(Product)
            .options(
                joinedload(Product.primary_listing).joinedload(ProductListing.source),
                joinedload(Product.memberships).joinedload(ProductListingMember.listing).joinedload(ProductListing.source),
                joinedload(Product.memberships).joinedload(ProductListingMember.listing).joinedload(ProductListing.variants),
                joinedload(Product.memberships).joinedload(ProductListingMember.listing).joinedload(ProductListing.images),
                joinedload(Product.presentation),
                joinedload(Product.price_override),
                joinedload(Product.gallery_images).joinedload(ProductListingGalleryImage.image_asset),
                joinedload(Product.gallery_images).joinedload(ProductListingGalleryImage.listing_image),
                joinedload(Product.designer),
                joinedload(Product.weight_rule),
            )
            .order_by(Product.updated_at.desc(), Product.id.desc())
        )
        if not include_merged:
            query = query.filter(Product.lifecycle_status != "merged")
        return query.offset(max(0, int(offset))).limit(max(1, int(limit))).all()

    def count_products(self, *, include_merged: bool = False) -> int:
        query = self.session.query(Product)
        if not include_merged:
            query = query.filter(Product.lifecycle_status != "merged")
        return int(query.count())

    def get_product(self, product_id: int) -> Product | None:
        return (
            self.session.query(Product)
            .options(
                joinedload(Product.primary_listing).joinedload(ProductListing.source),
                joinedload(Product.memberships).joinedload(ProductListingMember.listing).joinedload(ProductListing.source),
                joinedload(Product.memberships).joinedload(ProductListingMember.listing).joinedload(ProductListing.variants),
                joinedload(Product.memberships).joinedload(ProductListingMember.listing).joinedload(ProductListing.images),
                joinedload(Product.presentation),
                joinedload(Product.price_override),
                joinedload(Product.gallery_images).joinedload(ProductListingGalleryImage.image_asset),
                joinedload(Product.gallery_images).joinedload(ProductListingGalleryImage.listing_image),
                joinedload(Product.designer),
                joinedload(Product.weight_rule),
            )
            .filter(Product.id == int(product_id))
            .one_or_none()
        )

    def get_listing(self, listing_id: int) -> ProductListing | None:
        return (
            self.session.query(ProductListing)
            .options(joinedload(ProductListing.variants), joinedload(ProductListing.images), joinedload(ProductListing.source))
            .filter(ProductListing.id == int(listing_id))
            .one_or_none()
        )

    def get_listing_by_source_identity(self, *, source_id: int, external_id: str | None, url: str) -> ProductListing | None:
        query = self.session.query(ProductListing).filter(ProductListing.source_id == int(source_id))
        normalized_external_id = str(external_id or "").strip()
        if normalized_external_id:
            listing = query.filter(ProductListing.external_id == normalized_external_id).one_or_none()
            if listing is not None:
                return listing
        normalized_url = normalize_listing_url(url)
        return query.filter(ProductListing.url_normalized == normalized_url).one_or_none()

    def create_product(self, **kwargs) -> Product:
        entity = Product(**kwargs)
        self.session.add(entity)
        self.session.flush()
        return entity

    def create_listing(self, **kwargs) -> ProductListing:
        raw_url = str(kwargs.get("url") or "").strip()
        kwargs.setdefault("url_normalized", normalize_listing_url(raw_url))
        kwargs.setdefault("host_normalized", normalize_host(raw_url))
        entity = ProductListing(**kwargs)
        self.session.add(entity)
        self.session.flush()
        return entity

    def ensure_membership(self, *, product_id: int, listing_id: int) -> ProductListingMember:
        membership = (
            self.session.query(ProductListingMember)
            .filter(ProductListingMember.listing_id == int(listing_id))
            .one_or_none()
        )
        if membership is not None:
            membership.product_id = int(product_id)
            self.session.flush()
            return membership
        membership = ProductListingMember(product_id=int(product_id), listing_id=int(listing_id))
        self.session.add(membership)
        self.session.flush()
        return membership

    def list_product_listings(self, product_id: int) -> list[ProductListing]:
        return (
            self.session.query(ProductListing)
            .join(ProductListingMember, ProductListingMember.listing_id == ProductListing.id)
            .filter(ProductListingMember.product_id == int(product_id))
            .order_by(ProductListing.id.asc())
            .all()
        )

    def list_source_listings(self, source_id: int, *, ingest_mode: str | None = None) -> list[ProductListing]:
        query = self.session.query(ProductListing).filter(ProductListing.source_id == int(source_id))
        if ingest_mode:
            query = query.filter(ProductListing.ingest_mode == str(ingest_mode))
        return query.order_by(ProductListing.id.asc()).all()

    def get_product_by_listing(self, listing_id: int) -> Product | None:
        return (
            self.session.query(Product)
            .join(ProductListingMember, ProductListingMember.product_id == Product.id)
            .filter(ProductListingMember.listing_id == int(listing_id))
            .one_or_none()
        )

    def replace_variants(self, *, listing_id: int, variants: Iterable[dict]) -> None:
        self.session.query(ProductListingVariant).filter(ProductListingVariant.listing_id == int(listing_id)).delete()
        for position, item in enumerate(variants, start=1):
            self.session.add(
                ProductListingVariant(
                    listing_id=int(listing_id),
                    position=position,
                    source_ref_id=item.get("source_ref_id"),
                    sku=item.get("sku"),
                    title=item.get("title") or f"Variant {position}",
                    price_amount=item.get("price_amount"),
                    compare_at_price_amount=item.get("compare_at_price_amount"),
                    currency_code=item.get("currency_code"),
                    is_orderable=bool(item.get("is_orderable", True)),
                )
            )
        self.session.flush()

    def replace_listing_images(self, *, listing_id: int, image_urls: list[str]) -> list[ProductListingImage]:
        existing = (
            self.session.query(ProductListingImage)
            .filter(ProductListingImage.listing_id == int(listing_id))
            .order_by(ProductListingImage.position.asc())
            .all()
        )
        existing_by_position = {int(row.position): row for row in existing}
        keep_positions: set[int] = set()

        for position, url in enumerate(image_urls, start=1):
            keep_positions.add(position)
            entity = existing_by_position.get(position)
            if entity is None:
                entity = ProductListingImage(listing_id=int(listing_id), position=position, url=url)
                self.session.add(entity)
            else:
                entity.url = url

        for entity in existing:
            if int(entity.position) not in keep_positions:
                self.session.delete(entity)

        self.session.flush()
        return (
            self.session.query(ProductListingImage)
            .filter(ProductListingImage.listing_id == int(listing_id))
            .order_by(ProductListingImage.position.asc())
            .all()
        )

    def ensure_presentation(self, product_id: int) -> ProductPresentation:
        entity = (
            self.session.query(ProductPresentation)
            .filter(ProductPresentation.product_id == int(product_id))
            .one_or_none()
        )
        if entity is not None:
            return entity
        entity = ProductPresentation(product_id=int(product_id))
        self.session.add(entity)
        self.session.flush()
        return entity

    def get_price_override(self, product_id: int) -> ProductPriceOverride | None:
        return (
            self.session.query(ProductPriceOverride)
            .filter(ProductPriceOverride.product_id == int(product_id))
            .one_or_none()
        )

    def upsert_price_override(
        self,
        *,
        product_id: int,
        manual_price_rub: float,
        manual_compare_at_price_rub: float | None,
    ) -> ProductPriceOverride:
        entity = self.get_price_override(product_id)
        if entity is None:
            entity = ProductPriceOverride(
                product_id=int(product_id),
                manual_price_rub=manual_price_rub,
                manual_compare_at_price_rub=manual_compare_at_price_rub,
            )
            self.session.add(entity)
        else:
            entity.manual_price_rub = manual_price_rub
            entity.manual_compare_at_price_rub = manual_compare_at_price_rub
        self.session.flush()
        return entity

    def delete_price_override(self, product_id: int) -> None:
        entity = self.get_price_override(product_id)
        if entity is not None:
            self.session.delete(entity)
            self.session.flush()

    def list_gallery_scope(self, *, product_id: int, listing_id: int) -> list[ProductListingGalleryImage]:
        return (
            self.session.query(ProductListingGalleryImage)
            .options(joinedload(ProductListingGalleryImage.image_asset), joinedload(ProductListingGalleryImage.listing_image))
            .filter(
                ProductListingGalleryImage.product_id == int(product_id),
                ProductListingGalleryImage.listing_id == int(listing_id),
            )
            .order_by(ProductListingGalleryImage.position.asc(), ProductListingGalleryImage.id.asc())
            .all()
        )

    def replace_gallery_scope_with_source_images(
        self,
        *,
        product_id: int,
        listing_id: int,
        listing_images: list[ProductListingImage],
    ) -> None:
        existing = self.list_gallery_scope(product_id=product_id, listing_id=listing_id)
        for row in existing:
            self.session.delete(row)
        self.session.flush()

        for position, listing_image in enumerate(listing_images, start=1):
            self.session.add(
                ProductListingGalleryImage(
                    product_id=int(product_id),
                    listing_id=int(listing_id),
                    listing_image_id=int(listing_image.id),
                    position=position,
                    is_hidden=False,
                    origin_kind="source_image",
                )
            )
        self.session.flush()
