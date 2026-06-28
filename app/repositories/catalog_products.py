from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import and_, delete as sa_delete, func, update as sa_update
from sqlalchemy.orm import Session, joinedload, load_only, selectinload

from app.core.source_identity import normalize_host, normalize_listing_url
from app.models import (
    Product,
    ProductListing,
    ProductListingGalleryImage,
    ProductListingImage,
    ProductListingMember,
    ProductListingVariant,
    ProductPresentation,
    Source,
    SourceSetting,
    WeightRule,
)
from app.models.catalog_support import Designer


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
                joinedload(Product.gallery_images).joinedload(ProductListingGalleryImage.image_asset),
                joinedload(Product.gallery_images).joinedload(ProductListingGalleryImage.listing_image),
                joinedload(Product.designer),
                joinedload(Product.weight_rule),
            )
            .order_by(Product.updated_at.desc(), Product.id.desc())
        )
        if not include_merged:
            query = query.filter(Product.lifecycle_status == "active")
        return query.offset(max(0, int(offset))).limit(max(1, int(limit))).all()

    def count_products(self, *, include_merged: bool = False) -> int:
        query = self.session.query(Product)
        if not include_merged:
            query = query.filter(Product.lifecycle_status == "active")
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
                joinedload(Product.gallery_images).joinedload(ProductListingGalleryImage.image_asset),
                joinedload(Product.gallery_images).joinedload(ProductListingGalleryImage.listing_image),
                joinedload(Product.designer),
                joinedload(Product.weight_rule),
            )
            .filter(Product.id == int(product_id))
            .one_or_none()
        )

    def list_products_by_ids(self, product_ids: Iterable[int], *, include_merged: bool = True) -> list[Product]:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not normalized_ids:
            return []
        query = (
            self.session.query(Product)
            .options(
                joinedload(Product.primary_listing).joinedload(ProductListing.source),
                joinedload(Product.memberships).joinedload(ProductListingMember.listing).joinedload(ProductListing.source),
                joinedload(Product.memberships).joinedload(ProductListingMember.listing).joinedload(ProductListing.variants),
                joinedload(Product.memberships).joinedload(ProductListingMember.listing).joinedload(ProductListing.images),
                joinedload(Product.presentation),
                joinedload(Product.gallery_images).joinedload(ProductListingGalleryImage.image_asset),
                joinedload(Product.gallery_images).joinedload(ProductListingGalleryImage.listing_image),
                joinedload(Product.designer),
                joinedload(Product.weight_rule),
            )
            .filter(Product.id.in_(normalized_ids))
            .order_by(Product.id.asc())
        )
        if not include_merged:
            query = query.filter(Product.lifecycle_status == "active")
        return query.all()

    def list_products_for_admin_table_by_ids(self, product_ids: Iterable[int]) -> list[Product]:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not normalized_ids:
            return []
        return (
            self.session.query(Product)
            .options(
                joinedload(Product.designer),
                selectinload(Product.primary_listing).selectinload(ProductListing.source).selectinload(Source.setting),
                selectinload(Product.primary_listing).selectinload(ProductListing.variants),
                selectinload(Product.primary_listing).selectinload(ProductListing.images),
                selectinload(Product.memberships).selectinload(ProductListingMember.listing),
                joinedload(Product.weight_rule),
                selectinload(Product.gallery_images).selectinload(ProductListingGalleryImage.listing_image),
            )
            .filter(Product.id.in_(normalized_ids))
            .filter(Product.lifecycle_status == "active")
            .order_by(Product.id.asc())
            .all()
        )

    def list_products_for_pricing_example_by_ids(self, product_ids: Iterable[int]) -> list[Product]:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not normalized_ids:
            return []
        return (
            self.session.query(Product)
            .options(
                joinedload(Product.presentation),
                joinedload(Product.weight_rule),
                selectinload(Product.primary_listing).selectinload(ProductListing.source).selectinload(Source.setting),
                selectinload(Product.primary_listing).selectinload(ProductListing.variants),
                selectinload(Product.primary_listing).selectinload(ProductListing.images),
                selectinload(Product.gallery_images).selectinload(ProductListingGalleryImage.listing_image),
                selectinload(Product.gallery_images).selectinload(ProductListingGalleryImage.image_asset),
            )
            .filter(Product.id.in_(normalized_ids))
            .filter(Product.lifecycle_status == "active")
            .filter(Product.primary_listing_id.is_not(None))
            .order_by(Product.id.asc())
            .all()
        )

    def list_products_for_filter_assignment_by_ids(self, product_ids: Iterable[int]) -> list[Product]:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not normalized_ids:
            return []
        return (
            self.session.query(Product)
            .options(
                load_only(Product.id, Product.lifecycle_status, Product.dedup_status),
                selectinload(Product.memberships)
                .selectinload(ProductListingMember.listing)
                .load_only(
                    ProductListing.id,
                    ProductListing.source_title,
                    ProductListing.source_category_raw,
                    ProductListing.source_tags,
                ),
            )
            .filter(Product.id.in_(normalized_ids))
            .filter(Product.lifecycle_status == "active")
            .order_by(Product.id.asc())
            .all()
        )

    def list_active_product_ids_page(self, *, limit: int, offset: int) -> list[int]:
        return [
            int(product_id)
            for product_id, in (
                self.session.query(Product.id)
                .filter(Product.lifecycle_status == "active")
                .order_by(Product.id.asc())
                .offset(max(0, int(offset)))
                .limit(max(1, int(limit)))
                .all()
            )
        ]

    def count_active_products(self) -> int:
        return int(
            self.session.query(func.count(Product.id))
            .filter(Product.lifecycle_status == "active")
            .scalar()
            or 0
        )

    def list_products_for_dedup_by_ids(self, product_ids: Iterable[int]) -> list[Product]:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not normalized_ids:
            return []
        return (
            self.session.query(Product)
            .options(
                joinedload(Product.designer),
                joinedload(Product.primary_listing).joinedload(ProductListing.source).joinedload(Source.setting),
                joinedload(Product.primary_listing).joinedload(ProductListing.variants),
                joinedload(Product.primary_listing).joinedload(ProductListing.images),
                joinedload(Product.memberships).joinedload(ProductListingMember.listing).joinedload(ProductListing.source),
                joinedload(Product.weight_rule),
            )
            .filter(Product.id.in_(normalized_ids))
            .order_by(Product.id.asc())
            .all()
        )

    def list_dedup_candidate_rows(self) -> list:
        dedup_enabled_product_ids_sq = (
            self.session.query(ProductListingMember.product_id.label("product_id"))
            .join(ProductListing, ProductListing.id == ProductListingMember.listing_id)
            .join(Source, Source.id == ProductListing.source_id)
            .outerjoin(SourceSetting, SourceSetting.source_id == Source.id)
            .filter(func.coalesce(SourceSetting.dedup_enabled, True).is_(True))
            .group_by(ProductListingMember.product_id)
            .subquery()
        )
        first_image_sq = (
            self.session.query(
                ProductListingImage.listing_id.label("listing_id"),
                ProductListingImage.url.label("image_url"),
                func.row_number()
                .over(
                    partition_by=ProductListingImage.listing_id,
                    order_by=(ProductListingImage.position.asc(), ProductListingImage.id.asc()),
                )
                .label("rn"),
            )
            .subquery()
        )
        first_priced_variant_sq = (
            self.session.query(
                ProductListingVariant.listing_id.label("listing_id"),
                ProductListingVariant.price_amount.label("price_amount"),
                ProductListingVariant.currency_code.label("currency_code"),
                func.row_number()
                .over(
                    partition_by=ProductListingVariant.listing_id,
                    order_by=(ProductListingVariant.position.asc(), ProductListingVariant.id.asc()),
                )
                .label("rn"),
            )
            .filter(ProductListingVariant.price_amount.is_not(None))
            .subquery()
        )
        return (
            self.session.query(
                Product.id.label("product_id"),
                Product.visibility_status.label("visibility_status"),
                Product.manual_weight_grams.label("manual_weight_grams"),
                ProductPresentation.title_override.label("title_override"),
                WeightRule.weight_grams.label("rule_weight_grams"),
                Designer.name.label("designer_name"),
                ProductListing.id.label("listing_id"),
                ProductListing.ingest_mode.label("ingest_mode"),
                ProductListing.url.label("url"),
                ProductListing.source_title.label("source_title"),
                ProductListing.source_designer_raw.label("source_designer_raw"),
                ProductListing.source_weight_grams.label("source_weight_grams"),
                SourceSetting.show_images.label("show_images"),
                first_image_sq.c.image_url.label("image_url"),
                first_priced_variant_sq.c.price_amount.label("variant_price_amount"),
                first_priced_variant_sq.c.currency_code.label("variant_currency_code"),
            )
            .outerjoin(ProductPresentation, ProductPresentation.product_id == Product.id)
            .outerjoin(WeightRule, WeightRule.id == Product.weight_rule_id)
            .outerjoin(Designer, Designer.id == Product.designer_id)
            .outerjoin(ProductListing, ProductListing.id == Product.primary_listing_id)
            .outerjoin(Source, Source.id == ProductListing.source_id)
            .outerjoin(SourceSetting, SourceSetting.source_id == Source.id)
            .outerjoin(
                first_image_sq,
                and_(
                    first_image_sq.c.listing_id == ProductListing.id,
                    first_image_sq.c.rn == 1,
                ),
            )
            .outerjoin(
                first_priced_variant_sq,
                and_(
                    first_priced_variant_sq.c.listing_id == ProductListing.id,
                    first_priced_variant_sq.c.rn == 1,
                ),
            )
            .filter(Product.lifecycle_status == "active")
            .filter(Product.dedup_status == "independent")
            .filter(Product.id.in_(self.session.query(dedup_enabled_product_ids_sq.c.product_id)))
            .order_by(Product.updated_at.desc(), Product.id.desc())
            .all()
        )

    def get_dedup_candidate_fingerprint(self) -> tuple[int, str | None, str | None]:
        row = (
            self.session.query(
                func.count(Product.id),
                func.max(Product.updated_at),
                func.max(ProductListing.updated_at),
            )
            .outerjoin(ProductListing, ProductListing.id == Product.primary_listing_id)
            .filter(Product.lifecycle_status == "active")
            .filter(Product.dedup_status == "independent")
            .one()
        )
        product_count, max_product_updated_at, max_primary_listing_updated_at = row
        return (
            int(product_count or 0),
            max_product_updated_at.isoformat() if max_product_updated_at is not None else None,
            max_primary_listing_updated_at.isoformat() if max_primary_listing_updated_at is not None else None,
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
        if "gender" in kwargs and "source_gender" not in kwargs:
            kwargs["source_gender"] = kwargs["gender"]
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

    def ensure_owner_membership(self, *, product_id: int, listing_id: int) -> ProductListingMember:
        listing_id = int(listing_id)
        product_id = int(product_id)
        same_pair = (
            self.session.query(ProductListingMember)
            .filter(
                ProductListingMember.product_id == product_id,
                ProductListingMember.listing_id == listing_id,
            )
            .one_or_none()
        )
        if same_pair is not None and str(same_pair.membership_kind or "") == "owner":
            return same_pair
        current_owner = (
            self.session.query(ProductListingMember)
            .filter(
                ProductListingMember.listing_id == listing_id,
                ProductListingMember.membership_kind == "owner",
            )
            .one_or_none()
        )
        if same_pair is not None and current_owner is not None and int(current_owner.product_id) != product_id:
            self.session.delete(same_pair)
            self.session.flush()
            same_pair = None
        if current_owner is not None:
            current_owner.product_id = product_id
            current_owner.membership_kind = "owner"
            self.session.flush()
            return current_owner
        if same_pair is not None:
            same_pair.membership_kind = "owner"
            self.session.flush()
            return same_pair
        membership = ProductListingMember(product_id=product_id, listing_id=listing_id, membership_kind="owner")
        self.session.add(membership)
        self.session.flush()
        return membership

    def ensure_included_membership(self, *, product_id: int, listing_id: int) -> ProductListingMember:
        listing_id = int(listing_id)
        product_id = int(product_id)
        existing = (
            self.session.query(ProductListingMember)
            .filter(
                ProductListingMember.product_id == product_id,
                ProductListingMember.listing_id == listing_id,
            )
            .one_or_none()
        )
        if existing is not None:
            return existing
        membership = ProductListingMember(product_id=product_id, listing_id=listing_id, membership_kind="included")
        self.session.add(membership)
        self.session.flush()
        return membership

    def ensure_membership(self, *, product_id: int, listing_id: int) -> ProductListingMember:
        return self.ensure_owner_membership(product_id=product_id, listing_id=listing_id)

    def delete_product_hard(self, product_id: int) -> None:
        self.session.execute(sa_delete(Product).where(Product.id == int(product_id)))
        self.session.flush()
        self.session.expire_all()

    def set_product_primary_listing(self, *, product_id: int, primary_listing_id: int | None) -> None:
        self.session.execute(
            sa_update(Product)
            .where(Product.id == int(product_id))
            .values(primary_listing_id=(int(primary_listing_id) if primary_listing_id is not None else None))
        )
        self.session.flush()
        self.session.expire_all()

    def set_product_lifecycle_status(self, *, product_id: int, lifecycle_status: str) -> None:
        self.session.execute(
            sa_update(Product)
            .where(Product.id == int(product_id))
            .values(lifecycle_status=str(lifecycle_status))
        )
        self.session.flush()
        self.session.expire_all()

    def set_product_dedup_state(
        self,
        *,
        product_id: int,
        dedup_status: str,
        dedup_decision_id: int | None,
        dedup_target_product_id: int | None,
    ) -> None:
        self.session.execute(
            sa_update(Product)
            .where(Product.id == int(product_id))
            .values(
                dedup_status=str(dedup_status),
                dedup_decision_id=(int(dedup_decision_id) if dedup_decision_id is not None else None),
                dedup_target_product_id=(int(dedup_target_product_id) if dedup_target_product_id is not None else None),
            )
        )
        self.session.flush()
        self.session.expire_all()

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
            .filter(ProductListingMember.membership_kind == "owner")
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
                    pricing_mode=item.get("pricing_mode") or "source",
                    is_orderable=bool(item.get("is_orderable", True)),
                )
            )
        self.session.flush()

    def replace_listing_images(
        self,
        *,
        listing_id: int,
        image_urls: list[str],
    ) -> tuple[list[ProductListingImage], list[int]]:
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

        self.session.flush()
        stale_image_ids = [
            int(entity.id)
            for entity in existing
            if int(entity.position) not in keep_positions
        ]
        return (
            self.session.query(ProductListingImage)
            .filter(ProductListingImage.listing_id == int(listing_id))
            .filter(ProductListingImage.position.in_(keep_positions) if keep_positions else False)
            .order_by(ProductListingImage.position.asc())
            .all(),
            stale_image_ids,
        )

    def delete_listing_images(self, *, listing_image_ids: list[int]) -> None:
        normalized_ids = sorted({int(image_id) for image_id in listing_image_ids if int(image_id) > 0})
        if not normalized_ids:
            return
        (
            self.session.query(ProductListingImage)
            .filter(ProductListingImage.id.in_(normalized_ids))
            .delete(synchronize_session=False)
        )
        self.session.flush()

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

    def sync_gallery_scope_with_source_images(
        self,
        *,
        product_id: int,
        listing_id: int,
        listing_images: list[ProductListingImage],
    ) -> None:
        existing = self.list_gallery_scope(product_id=product_id, listing_id=listing_id)
        listing_images_by_url = {str(image.url): image for image in listing_images if str(image.url or "").strip()}
        synced_rows: list[dict[str, object]] = []
        seen_source_urls: set[str] = set()

        for row in existing:
            if row.image_asset_id is not None and str(row.origin_kind) == "uploaded_asset":
                synced_rows.append(
                    {
                        "origin_kind": "uploaded_asset",
                        "listing_image_id": None,
                        "image_asset_id": int(row.image_asset_id),
                        "is_hidden": False,
                    }
                )
                continue
            if row.listing_image is None:
                continue
            source_url = str(row.listing_image.url or "").strip()
            current_listing_image = listing_images_by_url.get(source_url)
            if current_listing_image is None:
                continue
            seen_source_urls.add(source_url)
            synced_rows.append(
                {
                    "origin_kind": "source_image",
                    "listing_image_id": int(current_listing_image.id),
                    "image_asset_id": None,
                    "is_hidden": bool(row.is_hidden),
                }
            )

        for listing_image in listing_images:
            source_url = str(listing_image.url or "").strip()
            if not source_url or source_url in seen_source_urls:
                continue
            synced_rows.append(
                {
                    "origin_kind": "source_image",
                    "listing_image_id": int(listing_image.id),
                    "image_asset_id": None,
                    "is_hidden": False,
                }
            )

        for row in existing:
            self.session.delete(row)
        self.session.flush()

        for position, row in enumerate(synced_rows, start=1):
            self.session.add(
                ProductListingGalleryImage(
                    product_id=int(product_id),
                    listing_id=int(listing_id),
                    listing_image_id=row["listing_image_id"],
                    image_asset_id=row["image_asset_id"],
                    position=position,
                    is_hidden=bool(row["is_hidden"]),
                    origin_kind=str(row["origin_kind"]),
                )
            )
        self.session.flush()
