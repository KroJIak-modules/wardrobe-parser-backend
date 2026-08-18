from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models import Designer, DesignerSourceName, Product, ProductListing, ProductListingMember, ProductPresentation
from app.services.catalog.designer_support import normalize_designer_text, slugify_designer_name


class DesignerCatalogSyncService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def active_source_brand_counts(self) -> dict[str, int]:
        self.db.flush()
        rows = (
            self.db.query(
                func.coalesce(ProductPresentation.brand_override_name, ProductListing.source_designer_raw).label("source_brand"),
                func.count(func.distinct(Product.id)).label("source_product_count"),
            )
            .select_from(ProductListing)
            .join(ProductListingMember, ProductListingMember.listing_id == ProductListing.id)
            .join(Product, Product.id == ProductListingMember.product_id)
            .outerjoin(ProductPresentation, ProductPresentation.product_id == Product.id)
            .filter(Product.lifecycle_status == "active")
            .filter(or_(ProductListing.orderability_status.is_(None), ProductListing.orderability_status != "unavailable"))
            .filter(func.length(func.trim(func.coalesce(ProductListing.source_designer_raw, ""))) > 0)
            .filter(func.length(func.trim(func.coalesce(ProductPresentation.brand_override_name, ProductListing.source_designer_raw, ""))) > 0)
            .group_by(func.coalesce(ProductPresentation.brand_override_name, ProductListing.source_designer_raw))
            .order_by(func.lower(func.coalesce(ProductPresentation.brand_override_name, ProductListing.source_designer_raw)).asc())
            .all()
        )
        return {
            normalize_designer_text(row.source_brand): int(row.source_product_count or 0)
            for row in rows
            if normalize_designer_text(row.source_brand)
        }

    def source_brand_product_counts(self) -> dict[str, tuple[int, int, int]]:
        """Return public, total, and unavailable active-product counts by source brand."""
        self.db.flush()
        source_brand = func.coalesce(ProductPresentation.brand_override_name, ProductListing.source_designer_raw)
        rows = (
            self.db.query(
                source_brand.label("source_brand"),
                func.count(func.distinct(Product.id)).label("total_count"),
                func.count(func.distinct(Product.id)).filter(
                    func.coalesce(ProductListing.orderability_status, "unavailable") != "orderable"
                ).label("unavailable_count"),
            )
            .select_from(ProductListing)
            .join(ProductListingMember, ProductListingMember.listing_id == ProductListing.id)
            .join(Product, Product.id == ProductListingMember.product_id)
            .outerjoin(ProductPresentation, ProductPresentation.product_id == Product.id)
            .filter(Product.lifecycle_status == "active")
            .filter(func.length(func.trim(func.coalesce(ProductListing.source_designer_raw, ""))) > 0)
            .filter(func.length(func.trim(source_brand)) > 0)
            .group_by(source_brand)
            .order_by(func.lower(source_brand).asc())
            .all()
        )
        result: dict[str, tuple[int, int, int]] = {}
        for row in rows:
            name = normalize_designer_text(row.source_brand)
            if not name:
                continue
            total_count = int(row.total_count or 0)
            unavailable_count = int(row.unavailable_count or 0)
            result[name] = (max(0, total_count - unavailable_count), total_count, unavailable_count)
        return result

    def reconcile(self, *, sync_product_links: bool = True) -> None:
        self.db.flush()
        active_counts = self.active_source_brand_counts()
        mappings = (
            self.db.query(DesignerSourceName)
            .options(joinedload(DesignerSourceName.designer))
            .order_by(DesignerSourceName.id.asc())
            .all()
        )
        mapping_by_source_name = {
            normalize_designer_text(mapping.source_name): mapping
            for mapping in mappings
            if normalize_designer_text(mapping.source_name)
        }

        designers = self.db.query(Designer).order_by(Designer.id.asc()).all()
        designers_by_id = {int(designer.id): designer for designer in designers}
        designers_by_name = {
            normalize_designer_text(designer.name): designer
            for designer in designers
            if normalize_designer_text(designer.name)
        }
        used_slugs = {
            str(designer.slug).strip()
            for designer in designers
            if str(designer.slug or "").strip()
        }

        def next_slug(name: str) -> str:
            base = slugify_designer_name(name)
            if base not in used_slugs:
                used_slugs.add(base)
                return base
            index = 2
            while f"{base}-{index}" in used_slugs:
                index += 1
            slug = f"{base}-{index}"
            used_slugs.add(slug)
            return slug

        def ensure_designer(name: str, current: Designer | None) -> Designer:
            normalized_name = normalize_designer_text(name)
            if current is not None and normalize_designer_text(current.name) == normalized_name:
                return current
            existing = designers_by_name.get(normalized_name)
            if existing is not None:
                return existing
            created = Designer(
                name=normalized_name,
                slug=next_slug(normalized_name),
                origin_kind="auto",
                is_admin_touched=False,
                is_enabled=True,
            )
            self.db.add(created)
            self.db.flush()
            designers_by_id[int(created.id)] = created
            designers_by_name[normalized_name] = created
            return created

        def mapping_is_protected(mapping: DesignerSourceName, linked: Designer | None) -> bool:
            if bool(mapping.is_admin_touched):
                return True
            if linked is None:
                return False
            return str(linked.origin_kind or "manual") == "manual" or bool(linked.is_admin_touched)

        for source_name in active_counts:
            mapping = mapping_by_source_name.get(source_name)
            if mapping is None:
                mapping = DesignerSourceName(
                    source_name=source_name,
                    designer_name=source_name,
                    is_enabled=True,
                    is_admin_touched=False,
                )
                self.db.add(mapping)
                self.db.flush()
                mapping_by_source_name[source_name] = mapping

        for source_name, mapping in mapping_by_source_name.items():
            normalized_source_name = normalize_designer_text(source_name)
            if normalized_source_name and not normalize_designer_text(mapping.source_name):
                mapping.source_name = normalized_source_name
            target_name = normalize_designer_text(mapping.designer_name) or normalized_source_name
            mapping.designer_name = target_name or None
            current_designer = designers_by_id.get(int(mapping.designer_id)) if mapping.designer_id is not None else mapping.designer
            if normalized_source_name in active_counts or mapping_is_protected(mapping, current_designer):
                if target_name:
                    linked = ensure_designer(target_name, current_designer)
                    mapping.designer_id = int(linked.id)
                    mapping.designer = linked
                else:
                    mapping.designer_id = None
                    mapping.designer = None
            else:
                mapping.designer_id = None
                mapping.designer = None

        self.db.flush()

        if sync_product_links:
            products = (
                self.db.query(Product)
                .options(joinedload(Product.primary_listing), joinedload(Product.presentation), joinedload(Product.designer))
                .filter(Product.lifecycle_status == "active")
                .all()
            )
            for product in products:
                listing = product.primary_listing
                desired_designer_id: int | None = None
                if listing is not None:
                    override_name = normalize_designer_text(getattr(getattr(product, "presentation", None), "brand_override_name", None))
                    if override_name:
                        mapping = mapping_by_source_name.get(override_name)
                        linked = (
                            designers_by_id.get(int(mapping.designer_id))
                            if mapping is not None and mapping.designer_id is not None
                            else None
                        )
                        if mapping is not None and bool(mapping.is_enabled) and (override_name in active_counts or mapping_is_protected(mapping, linked)):
                            desired_designer_id = int(mapping.designer_id) if mapping.designer_id is not None else None
                        else:
                            existing = designers_by_name.get(override_name)
                            if existing is not None:
                                desired_designer_id = int(existing.id)
                    else:
                        source_name = normalize_designer_text(listing.source_designer_raw)
                        if source_name:
                            mapping = mapping_by_source_name.get(source_name)
                            linked = (
                                designers_by_id.get(int(mapping.designer_id))
                                if mapping is not None and mapping.designer_id is not None
                                else None
                            )
                            if mapping is not None and bool(mapping.is_enabled):
                                if source_name in active_counts or mapping_is_protected(mapping, linked):
                                    desired_designer_id = int(mapping.designer_id) if mapping.designer_id is not None else None
                if int(product.designer_id or 0) != int(desired_designer_id or 0):
                    product.designer_id = desired_designer_id

        self.db.flush()

        for source_name, mapping in list(mapping_by_source_name.items()):
            if source_name in active_counts:
                continue
            linked = designers_by_id.get(int(mapping.designer_id)) if mapping.designer_id is not None else mapping.designer
            if mapping_is_protected(mapping, linked):
                continue
            self.db.delete(mapping)
            mapping_by_source_name.pop(source_name, None)

        self.db.flush()

        referenced_designer_ids = {
            int(designer_id)
            for designer_id, in self.db.query(Product.designer_id).filter(Product.designer_id.is_not(None)).all()
        }
        referenced_designer_ids.update(
            int(mapping.designer_id)
            for mapping in mapping_by_source_name.values()
            if mapping.designer_id is not None
        )

        for designer_id, designer in list(designers_by_id.items()):
            if str(designer.origin_kind or "manual") == "manual":
                continue
            if bool(designer.is_admin_touched):
                continue
            if designer_id in referenced_designer_ids:
                continue
            self.db.delete(designer)
            designers_by_id.pop(designer_id, None)
            normalized_name = normalize_designer_text(designer.name)
            if designers_by_name.get(normalized_name) is designer:
                designers_by_name.pop(normalized_name, None)

        self.db.flush()
