from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import CatalogPresenceState, Designer, Product, ProductListing, ProductListingMember, SourceSetting
from app.repositories.catalog_again import CatalogPresenceRepository
from app.services.catalog.designer_support import normalize_designer_text
from app.services.catalog.public_product_policy import public_product_candidate_condition


class CatalogPresenceService:
    """Tracks automatic 0 -> positive catalog reappearance transitions."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.states = CatalogPresenceRepository(db)

    def initialize_product_if_missing(self, product_id: int) -> None:
        key = str(int(product_id))
        existing = self.db.query(CatalogPresenceState.entity_key).filter_by(
            entity_kind="product", entity_key=key
        ).first()
        if existing is None:
            self.refresh_product_ids([product_id])

    def refresh_product_ids(self, product_ids: Iterable[int]) -> None:
        ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not ids:
            return
        rows = (
            self.db.query(
                Product.id.label("product_id"),
                func.count(ProductListing.id)
                .filter(ProductListing.orderability_status == "orderable")
                .label("available_count"),
            )
            .select_from(Product)
            .outerjoin(ProductListingMember, ProductListingMember.product_id == Product.id)
            .outerjoin(ProductListing, ProductListing.id == ProductListingMember.listing_id)
            .filter(Product.id.in_(ids))
            .group_by(Product.id)
            .all()
        )
        counts = {str(int(row.product_id)): int(row.available_count or 0) for row in rows}
        counts.update({str(product_id): 0 for product_id in ids if str(product_id) not in counts})
        self.states.sync_counts(entity_kind="product", counts=counts, complete_snapshot=False)

    def refresh_brands(
        self,
        counts: Mapping[str, int],
        *,
        ignored_keys: Iterable[str] = (),
        emit_reappearance: bool = True,
    ) -> None:
        normalized = {
            normalize_designer_text(key): max(0, int(value))
            for key, value in counts.items()
            if normalize_designer_text(key)
        }
        ignored = {
            normalize_designer_text(key)
            for key in ignored_keys
            if normalize_designer_text(key)
        }
        # Manual mappings are intentionally not advanced through automatic
        # absence/reappearance transitions.
        for key in ignored:
            normalized.pop(key, None)
        self.states.sync_counts(
            entity_kind="brand",
            counts=normalized,
            known_keys=ignored,
            ignored_keys=ignored,
            emit_reappearance=emit_reappearance,
        )

    def refresh_designers(self, counts: Mapping[str, int], *, emit_reappearance: bool = True) -> None:
        normalized = {
            normalize_designer_text(key): max(0, int(value))
            for key, value in counts.items()
            if normalize_designer_text(key)
        }
        self.states.sync_counts(entity_kind="designer", counts=normalized, emit_reappearance=emit_reappearance)

    def current_public_designer_counts(self) -> dict[str, int]:
        rows = (
            self.db.query(
                Designer.name.label("designer_name"),
                func.count(Product.id).label("product_count"),
            )
            .select_from(Product)
            .join(Designer, Designer.id == Product.designer_id)
            .join(ProductListing, ProductListing.id == Product.primary_listing_id)
            .outerjoin(SourceSetting, SourceSetting.source_id == ProductListing.source_id)
            .filter(public_product_candidate_condition())
            .group_by(Designer.name)
            .all()
        )
        return {
            normalize_designer_text(row.designer_name): int(row.product_count or 0)
            for row in rows
            if normalize_designer_text(row.designer_name)
        }
