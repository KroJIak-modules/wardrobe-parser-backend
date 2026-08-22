from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models import Designer, DesignerSourceName, Product, ProductListing, ProductPresentation, SourceSetting
from app.services.catalog.designer_support import normalize_designer_text, slugify_designer_name
from app.services.catalog.public_product_policy import public_product_candidate_condition, public_product_condition
from app.services.catalog.product_visibility_service import ProductVisibilityService


class DesignerCatalogSyncService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _source_brand_count_query(self):
        source_brand = func.coalesce(ProductPresentation.brand_override_name, ProductListing.source_designer_raw)
        return source_brand, (
            self.db.query(source_brand.label("source_brand"), Product.id.label("product_id"))
            .select_from(Product)
            .join(ProductListing, ProductListing.id == Product.primary_listing_id)
            .outerjoin(ProductPresentation, ProductPresentation.product_id == Product.id)
            .outerjoin(SourceSetting, SourceSetting.source_id == ProductListing.source_id)
            .filter(Product.lifecycle_status == "active")
            .filter(func.length(func.trim(func.coalesce(ProductListing.source_designer_raw, ""))) > 0)
            .filter(func.length(func.trim(source_brand)) > 0)
        )

    @staticmethod
    def _counts_by_source_brand(rows) -> dict[str, int]:
        return {
            normalize_designer_text(row.source_brand): int(row.source_product_count or 0)
            for row in rows
            if normalize_designer_text(row.source_brand)
        }

    def source_brand_counts(self, *, public_candidates_only: bool) -> dict[str, int]:
        """Count active primary-listing products by brand using the shared public policy."""
        self.db.flush()
        source_brand, query = self._source_brand_count_query()
        if public_candidates_only:
            query = query.filter(public_product_candidate_condition())
        rows = (
            query.with_entities(
                source_brand.label("source_brand"),
                func.count(Product.id).label("source_product_count"),
            )
            .group_by(source_brand)
            .order_by(func.lower(source_brand).asc())
            .all()
        )
        return self._counts_by_source_brand(rows)

    def source_brand_product_counts(self) -> dict[str, tuple[int, int, int]]:
        """Return public, total, and non-public active primary-product counts by source brand."""
        self.db.flush()
        source_brand, query = self._source_brand_count_query()
        rows = (
            query.with_entities(
                source_brand.label("source_brand"),
                func.count(Product.id).label("total_count"),
                func.count(Product.id).filter(public_product_condition()).label("public_count"),
            )
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
            public_count = int(row.public_count or 0)
            result[name] = (public_count, total_count, max(0, total_count - public_count))
        return result

    def sync_product_links_for_source_brands(self, source_brands: list[str]) -> None:
        """Restore current designer links for a small, explicitly changed brand set."""
        normalized_brands = {normalize_designer_text(source_brand) for source_brand in source_brands}
        normalized_brands.discard("")
        if not normalized_brands:
            return

        mappings = (
            self.db.query(DesignerSourceName)
            .options(joinedload(DesignerSourceName.designer))
            .filter(func.lower(DesignerSourceName.source_name).in_({brand.lower() for brand in normalized_brands}))
            .order_by(DesignerSourceName.is_admin_touched.desc(), DesignerSourceName.id.asc())
            .all()
        )
        designer_ids: dict[str, int] = {}
        for mapping in mappings:
            source_brand = normalize_designer_text(mapping.source_name)
            if source_brand and mapping.designer_id is not None:
                designer_ids.setdefault(source_brand, int(mapping.designer_id))
        source_brand = func.coalesce(ProductPresentation.brand_override_name, ProductListing.source_designer_raw)
        products = (
            self.db.query(Product)
            .join(ProductListing, ProductListing.id == Product.primary_listing_id)
            .outerjoin(ProductPresentation, ProductPresentation.product_id == Product.id)
            .filter(Product.lifecycle_status == "active")
            .filter(func.lower(source_brand).in_({brand.lower() for brand in normalized_brands}))
            .all()
        )
        for product in products:
            source_brand_name = normalize_designer_text(
                getattr(product.presentation, "brand_override_name", None)
                or getattr(product.primary_listing, "source_designer_raw", None)
            )
            if source_brand_name in designer_ids:
                product.designer_id = designer_ids[source_brand_name]
        self.db.flush()

    def reconcile(self, *, sync_product_links: bool = True, refresh_visibility: bool = True) -> None:
        self.db.flush()
        source_counts = self.source_brand_counts(public_candidates_only=False)
        public_candidate_source_counts = self.source_brand_counts(public_candidates_only=True)
        visibility_source_names: set[str] = set()
        mappings = (
            self.db.query(DesignerSourceName)
            .options(joinedload(DesignerSourceName.designer))
            .order_by(DesignerSourceName.is_admin_touched.desc(), DesignerSourceName.id.asc())
            .all()
        )
        mapping_by_source_name: dict[str, DesignerSourceName] = {}
        for mapping in mappings:
            source_name = normalize_designer_text(mapping.source_name)
            if source_name:
                mapping_by_source_name.setdefault(source_name, mapping)

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

        for source_name in source_counts:
            mapping = mapping_by_source_name.get(source_name)
            if mapping is None:
                visibility_source_names.add(source_name)
                mapping = DesignerSourceName(
                    source_name=source_name,
                    designer_name=source_name,
                    is_enabled=source_name in public_candidate_source_counts,
                    is_admin_touched=False,
                )
                self.db.add(mapping)
                self.db.flush()
                mapping_by_source_name[source_name] = mapping
            elif not bool(mapping.is_admin_touched):
                next_is_enabled = source_name in public_candidate_source_counts
                if bool(mapping.is_enabled) != next_is_enabled:
                    visibility_source_names.add(source_name)
                    mapping.is_enabled = next_is_enabled

        for source_name, mapping in mapping_by_source_name.items():
            normalized_source_name = normalize_designer_text(source_name)
            if normalized_source_name and not normalize_designer_text(mapping.source_name):
                mapping.source_name = normalized_source_name
            target_name = normalize_designer_text(mapping.designer_name) or normalized_source_name
            mapping.designer_name = target_name or None
            current_designer = designers_by_id.get(int(mapping.designer_id)) if mapping.designer_id is not None else mapping.designer
            if normalized_source_name in source_counts or mapping_is_protected(mapping, current_designer):
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
                        if mapping is not None:
                            if override_name in source_counts or mapping_is_protected(mapping, linked):
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
                            if mapping is not None:
                                if source_name in source_counts or mapping_is_protected(mapping, linked):
                                    desired_designer_id = int(mapping.designer_id) if mapping.designer_id is not None else None
                if int(product.designer_id or 0) != int(desired_designer_id or 0):
                    product.designer_id = desired_designer_id

        self.db.flush()

        for source_name, mapping in list(mapping_by_source_name.items()):
            if source_name in source_counts:
                continue
            linked = designers_by_id.get(int(mapping.designer_id)) if mapping.designer_id is not None else mapping.designer
            if mapping_is_protected(mapping, linked):
                continue
            self.db.delete(mapping)
            mapping_by_source_name.pop(source_name, None)

        self.db.flush()
        if refresh_visibility:
            ProductVisibilityService(self.db).refresh_source_brands(visibility_source_names)

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
