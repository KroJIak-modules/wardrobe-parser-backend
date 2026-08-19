from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import DesignerSourceName, Product, ProductListing, ProductPresentation, SourceSetting
from app.services.catalog.designer_support import normalize_designer_text


class ProductVisibilityService:
    """Materialize public product visibility from independent manual and catalog causes."""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _effective_source_brand(product: Product) -> str:
        presentation = product.presentation
        listing = product.primary_listing
        return normalize_designer_text(
            getattr(presentation, "brand_override_name", None)
            or getattr(listing, "source_designer_raw", None)
        )

    def refresh_product_ids(self, product_ids: Iterable[int]) -> list[int]:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not normalized_ids:
            return []

        self.db.flush()
        products = (
            self.db.query(Product)
            .options(joinedload(Product.primary_listing), joinedload(Product.presentation))
            .filter(Product.id.in_(normalized_ids))
            .filter(Product.lifecycle_status == "active")
            .all()
        )
        source_ids = {int(product.primary_listing.source_id) for product in products if product.primary_listing is not None}
        hidden_source_ids = {
            int(source_id)
            for source_id, in (
                self.db.query(SourceSetting.source_id)
                .populate_existing()
                .filter(SourceSetting.source_id.in_(source_ids))
                .filter(SourceSetting.hide_auto_added_products.is_(True))
                .all()
            )
        }
        source_brands = {self._effective_source_brand(product) for product in products}
        source_brands.discard("")
        disabled_source_brands = {
            normalize_designer_text(source_name)
            for source_name, in (
                self.db.query(DesignerSourceName.source_name)
                .filter(func.lower(DesignerSourceName.source_name).in_({name.lower() for name in source_brands}))
                .filter(DesignerSourceName.is_enabled.is_(False))
                .all()
            )
        }

        changed_product_ids: list[int] = []
        for product in products:
            primary_listing = product.primary_listing
            is_hidden_by_source = primary_listing is not None and int(primary_listing.source_id) in hidden_source_ids
            is_hidden_by_brand = self._effective_source_brand(product) in disabled_source_brands
            expected_status = "hidden" if bool(product.is_manually_hidden) or is_hidden_by_source or is_hidden_by_brand else "visible"
            if product.visibility_status != expected_status:
                product.visibility_status = expected_status
                changed_product_ids.append(int(product.id))
        self.db.flush()
        return changed_product_ids

    def refresh_source_ids(self, source_ids: Iterable[int]) -> list[int]:
        normalized_ids = sorted({int(source_id) for source_id in source_ids if int(source_id) > 0})
        if not normalized_ids:
            return []
        product_ids = [
            int(product_id)
            for product_id, in (
                self.db.query(Product.id)
                .join(ProductListing, ProductListing.id == Product.primary_listing_id)
                .filter(ProductListing.source_id.in_(normalized_ids))
                .all()
            )
        ]
        return self.refresh_product_ids(product_ids)

    def refresh_source_brands(self, source_brands: Iterable[str]) -> list[int]:
        normalized_names = {normalize_designer_text(name) for name in source_brands}
        normalized_names.discard("")
        if not normalized_names:
            return []
        product_ids = [
            int(product_id)
            for product_id, in (
                self.db.query(Product.id)
                .join(ProductListing, ProductListing.id == Product.primary_listing_id)
                .outerjoin(ProductPresentation, ProductPresentation.product_id == Product.id)
                .filter(
                    func.lower(
                        func.coalesce(ProductPresentation.brand_override_name, ProductListing.source_designer_raw, "")
                    ).in_({name.lower() for name in normalized_names})
                )
                .all()
            )
        ]
        return self.refresh_product_ids(product_ids)

    def refresh_all(self) -> list[int]:
        product_ids = [int(product_id) for product_id, in self.db.query(Product.id).filter(Product.lifecycle_status == "active").all()]
        return self.refresh_product_ids(product_ids)
