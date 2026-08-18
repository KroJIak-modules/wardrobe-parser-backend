from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, inspect, or_
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import ValidationError
from app.models import (
    CustomCatalog,
    CustomCatalogProduct,
    Designer,
    DesignerSourceName,
    FilterAssignmentRuntimeState,
    Product,
    ProductFilterAssignment,
    ProductListing,
    ProductListingMember,
    ProductPresentation,
    ShowcaseCategory,
    Source,
)
from app.repositories.catalog_taxonomy import SHOWCASE_CATEGORY_ORDER
from app.schemas.taxonomy import TaxonomyWriteState
from app.services.catalog.designer_catalog_sync_service import DesignerCatalogSyncService
from app.services.catalog.designer_support import normalize_designer_text, slugify_designer_name
from app.services.catalog.filter_assignment_service import ProductFilterAssignmentService
from app.services.catalog.product_query_service import ProductQueryService
from app.services.catalog.taxonomy_service import TaxonomyService


class AdminEditorService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.taxonomy = TaxonomyService(db)
        self.products = ProductQueryService(db)

    @staticmethod
    def _normalize_text(value: object | None) -> str:
        return normalize_designer_text(value)

    @staticmethod
    def _normalize_optional_positive_int(value: object | None) -> int | None:
        if value is None:
            return None
        raw = str(value).strip()
        if not raw:
            return None
        try:
            candidate = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Некорректный идентификатор изображения") from exc
        if candidate <= 0:
            raise ValidationError("Некорректный идентификатор изображения")
        return candidate

    @staticmethod
    def _category_behavior(code: str) -> tuple[str, str | None]:
        normalized = str(code or "").strip().lower()
        if normalized == "new":
            return "new", None
        if normalized == "designers":
            return "designers", None
        if normalized in {"men", "women"}:
            return "gender", normalized
        return "sale", None

    @staticmethod
    def _is_showcase_attachment_allowed(category_code: object, attachment_kind: object) -> bool:
        normalized_code = str(category_code or "").strip()
        normalized_kind = str(attachment_kind or "").strip()
        if normalized_code == "new":
            return normalized_kind == "custom_catalog"
        if normalized_code in {"men", "women"}:
            return normalized_kind == "filter"
        return False

    @staticmethod
    def _sort_category_payloads(categories: list[dict]) -> list[dict]:
        order_index = {code: index for index, code in enumerate(SHOWCASE_CATEGORY_ORDER)}
        return sorted(
            categories,
            key=lambda category: (
                order_index.get(str(category.get("slug") or "").strip(), len(order_index)),
                str(category.get("slug") or "").strip(),
            ),
        )

    def _filter_product_counts_by_slug(self) -> dict[str, int]:
        state = (
            self.db.query(FilterAssignmentRuntimeState)
            .filter(FilterAssignmentRuntimeState.id == 1)
            .one_or_none()
        )
        revision = int(getattr(state, "applied_revision", 0) or 0)
        if revision <= 0:
            return {}
        return {
            str(slug): int(count)
            for slug, count in (
                self.db.query(
                    ProductFilterAssignment.filter_slug,
                    func.count(func.distinct(ProductFilterAssignment.product_id)).label("product_count"),
                )
                .filter(ProductFilterAssignment.revision == revision)
                .group_by(ProductFilterAssignment.filter_slug)
                .all()
            )
            if str(slug or "").strip()
        }

    @staticmethod
    def _serialize_manual_product_payload(payload: dict) -> dict | None:
        if payload is None:
            return None
        image_urls = payload.get("image_urls") or []
        return {
            "product_id": int(payload["id"]),
            "source_name": str(payload.get("source_name") or "").strip(),
            "url": str(payload.get("url") or "").strip(),
            "designer_name": str(payload.get("designer_name") or payload.get("source_designer_name") or "").strip(),
            "title": str(payload.get("title") or "").strip(),
            "image_url": str(image_urls[0]).strip() if image_urls else None,
            "visibility_status": "hidden" if str(payload.get("visibility_status") or "").strip().lower() == "hidden" else "visible",
            "orderability_status": str(payload.get("orderability_status") or "").strip().lower() or "unavailable",
            "status_reason": str(payload.get("status_reason") or "").strip() or None,
            "assigned_filter_titles": [str(item).strip() for item in payload.get("internal_category_names") or [] if str(item).strip()],
        }

    def _custom_catalog_titles_by_product_ids(self, product_ids: list[int]) -> dict[int, list[str]]:
        if not product_ids:
            return {}
        rows = (
            self.db.query(CustomCatalogProduct.product_id, CustomCatalog.title)
            .join(CustomCatalog, CustomCatalog.id == CustomCatalogProduct.catalog_id)
            .filter(CustomCatalogProduct.product_id.in_(product_ids))
            .order_by(CustomCatalogProduct.product_id.asc(), CustomCatalog.title.asc(), CustomCatalog.id.asc())
            .all()
        )
        result: dict[int, list[str]] = defaultdict(list)
        for product_id, title in rows:
            normalized_title = self._normalize_text(title)
            if normalized_title and normalized_title not in result[int(product_id)]:
                result[int(product_id)].append(normalized_title)
        return result

    def _admin_product_payload_index(self, product_ids: list[int] | None = None) -> dict[int, dict]:
        if product_ids is not None and not product_ids:
            return {}
        query = (
            self.db.query(Product)
            .options(
                joinedload(Product.designer),
                joinedload(Product.primary_listing).joinedload(ProductListing.source),
                joinedload(Product.primary_listing).joinedload(ProductListing.images),
                joinedload(Product.memberships).joinedload(ProductListingMember.listing),
            )
            .filter(Product.lifecycle_status == "active")
        )
        if product_ids is not None:
            query = query.filter(Product.id.in_(product_ids))
        products = query.order_by(Product.id.asc()).all()
        product_ids = [int(product.id) for product in products]
        catalog_titles_by_product_id = self._custom_catalog_titles_by_product_ids(product_ids)
        result: dict[int, dict] = {}
        for product in products:
            primary_listing = self.products._resolved_primary_listing(product)
            effective_orderability_status, effective_status_reason = self.products._effective_orderability_state(product, primary_listing)
            image_url = None
            if primary_listing is not None and primary_listing.images:
                first_image = sorted(primary_listing.images, key=lambda item: int(item.position or 0))[0]
                image_url = str(first_image.url or "").strip() or None
            assigned_titles = self.products._matched_filter_labels(product)
            for title in catalog_titles_by_product_id.get(int(product.id), []):
                if title not in assigned_titles:
                    assigned_titles.append(title)
            result[int(product.id)] = {
                "id": int(product.id),
                "source_name": (
                    str(getattr(getattr(primary_listing, "source", None), "name", "") or "") or None
                    if primary_listing is not None and self.products._is_business_source_listing(primary_listing)
                    else None
                ),
                "url": (
                    str(primary_listing.url)
                    if primary_listing is not None and self.products._is_business_source_listing(primary_listing)
                    else None
                ),
                "designer_name": (
                    self._normalize_text(getattr(product.designer, "name", None))
                    or self._normalize_text(getattr(product.presentation, "brand_override_name", None))
                    or self._normalize_text(getattr(primary_listing, "source_designer_raw", None))
                    or None
                ),
                "title": self.products._effective_title(product, primary_listing),
                "image_urls": ([image_url] if image_url else []),
                "visibility_status": str(product.visibility_status or "visible"),
                "orderability_status": effective_orderability_status,
                "status_reason": effective_status_reason,
                "internal_category_names": assigned_titles,
            }
        return result

    def _search_taxonomy_product_ids(self, query: str, limit: int) -> list[int]:
        normalized = self._normalize_text(query)
        if not normalized:
            return []
        pattern = f"%{normalized.casefold()}%"
        rows = (
            self.db.query(Product.id)
            .outerjoin(Product.designer)
            .outerjoin(Product.presentation)
            .outerjoin(Product.primary_listing)
            .outerjoin(Source, Source.id == ProductListing.source_id)
            .filter(Product.lifecycle_status == "active")
            .filter(
                or_(
                    func.lower(func.coalesce(Designer.name, "")).like(pattern),
                    func.lower(func.coalesce(ProductPresentation.brand_override_name, ProductListing.source_designer_raw, "")).like(pattern),
                    func.lower(func.coalesce(ProductListing.source_title, "")).like(pattern),
                    func.lower(func.coalesce(Source.name, "")).like(pattern),
                )
            )
            .order_by(Product.id.asc())
            .limit(limit)
            .all()
        )
        return [int(product_id) for product_id, in rows]

    def search_taxonomy_product_library(self, query: str, limit: int = 12) -> list[dict]:
        product_ids = self._search_taxonomy_product_ids(query=query, limit=limit)
        payloads_by_product_id = self._admin_product_payload_index(product_ids)
        return self._serialize_manual_products(product_ids, payloads_by_product_id)

    def _serialize_manual_product(self, product_id: int, payloads_by_product_id: dict[int, dict] | None = None) -> dict | None:
        payload = (
            payloads_by_product_id.get(int(product_id))
            if payloads_by_product_id is not None
            else self.products.get_product_payload(int(product_id), audience="admin")
        )
        if payload is None:
            return None
        return self._serialize_manual_product_payload(payload)

    def _serialize_manual_products(self, product_ids: list[int], payloads_by_product_id: dict[int, dict] | None = None) -> list[dict]:
        items: list[dict] = []
        for product_id in product_ids:
            payload = self._serialize_manual_product(int(product_id), payloads_by_product_id)
            if payload is not None:
                items.append(payload)
        return items

    def list_designer_editor_state(self, *, reconcile: bool = False) -> dict:
        sync = DesignerCatalogSyncService(self.db)
        if reconcile:
            sync.reconcile(sync_product_links=False)
        count_by_source_name = sync.source_brand_product_counts()
        mapping_rows = self.db.query(DesignerSourceName).all()
        mapping_by_source_name = {
            self._normalize_text(row.source_name): row
            for row in mapping_rows
            if self._normalize_text(row.source_name)
        }

        all_source_names = sorted(set(count_by_source_name), key=lambda item: item)

        result_rows: list[dict] = []
        for source_name in all_source_names:
            mapping = mapping_by_source_name.get(source_name)
            public_count, total_count, unavailable_count = count_by_source_name.get(source_name, (0, 0, 0))
            mapped_designer_name = self._normalize_text(getattr(mapping, "designer_name", None)) if mapping is not None else ""
            result_rows.append(
                {
                    "source_brand": source_name,
                    "source_product_count": total_count,
                    "source_unavailable_product_count": unavailable_count,
                    "source_public_product_count": public_count,
                    "designer_name": mapped_designer_name or source_name,
                    "include_in_designers": bool(True if mapping is None else getattr(mapping, "is_enabled", True)),
                }
            )

        designers = [
            {
                "id": str(designer.id),
                "name": self._normalize_text(designer.name),
                "description": str(designer.description or "").strip(),
            }
            for designer in (
                self.db.query(Designer)
                .order_by(Designer.name.asc(), Designer.id.asc())
                .all()
            )
        ]
        return {"rows": result_rows, "designers": designers}

    def save_designer_editor_state(self, payload: dict) -> dict:
        rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
        designers = payload.get("designers") if isinstance(payload.get("designers"), list) else []

        existing_designers = {
            int(row.id): row
            for row in self.db.query(Designer).order_by(Designer.id.asc()).all()
        }
        used_slugs = {
            str(row.slug).strip()
            for row in existing_designers.values()
            if str(row.slug or "").strip()
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

        desired_designer_ids: set[int] = set()

        for raw_designer in designers:
            if not isinstance(raw_designer, dict):
                continue
            designer_name = self._normalize_text(raw_designer.get("name"))
            if not designer_name:
                continue
            raw_id = self._normalize_text(raw_designer.get("id"))
            entity: Designer | None = None
            if raw_id.isdigit():
                entity = existing_designers.get(int(raw_id))
            if entity is None:
                entity = Designer(
                    name=designer_name,
                    slug=next_slug(designer_name),
                    origin_kind="manual",
                    is_admin_touched=True,
                    is_enabled=True,
                )
                self.db.add(entity)
                self.db.flush()
                existing_designers[int(entity.id)] = entity
            next_description = str(raw_designer.get("description") or "").strip() or None
            if (
                self._normalize_text(entity.name) != designer_name
                or (entity.description or None) != next_description
            ):
                entity.is_admin_touched = True
            entity.name = designer_name
            entity.description = next_description
            entity.is_enabled = True
            desired_designer_ids.add(int(entity.id))
        existing_source_mappings = {
            self._normalize_text(row.source_name): row
            for row in self.db.query(DesignerSourceName).order_by(DesignerSourceName.id.asc()).all()
            if self._normalize_text(row.source_name)
        }

        seen_source_names: set[str] = set()
        for raw_row in rows:
            if not isinstance(raw_row, dict):
                continue
            source_name = self._normalize_text(raw_row.get("source_brand"))
            if not source_name:
                continue
            source_key = source_name
            if source_key in seen_source_names:
                continue
            seen_source_names.add(source_key)
            include_in_designers = bool(raw_row.get("include_in_designers"))
            designer_name = self._normalize_text(raw_row.get("designer_name"))
            mapping = existing_source_mappings.get(source_name)
            if mapping is None:
                mapping = DesignerSourceName(
                    source_name=source_name,
                    designer_name=source_name,
                    is_enabled=True,
                    is_admin_touched=False,
                )
                self.db.add(mapping)
                self.db.flush()
                existing_source_mappings[source_name] = mapping
            next_designer_name = designer_name or source_name
            if (
                self._normalize_text(mapping.designer_name) != next_designer_name
                or bool(mapping.is_enabled) != include_in_designers
            ):
                mapping.is_admin_touched = True
            mapping.designer_name = next_designer_name
            mapping.is_enabled = include_in_designers

        DesignerCatalogSyncService(self.db).reconcile(sync_product_links=True)

        referenced_designer_ids = {
            int(row[0])
            for row in (
                self.db.query(Product.designer_id)
                .filter(Product.designer_id.is_not(None))
                .all()
            )
        }
        referenced_designer_ids.update(
            int(row[0])
            for row in (
                self.db.query(DesignerSourceName.designer_id)
                .filter(DesignerSourceName.designer_id.is_not(None))
                .all()
            )
        )
        for designer_id, entity in list(existing_designers.items()):
            if inspect(entity).deleted:
                existing_designers.pop(designer_id, None)
                continue
            is_auto_untouched = str(entity.origin_kind or "manual") == "auto" and not bool(entity.is_admin_touched)
            if not is_auto_untouched and designer_id in desired_designer_ids:
                continue
            if designer_id in referenced_designer_ids:
                continue
            self.db.delete(entity)
            existing_designers.pop(designer_id, None)

        self.db.flush()
        return self.list_designer_editor_state(reconcile=False)

    def set_designer_source_enabled(self, *, source_brand: str, include_in_designers: bool) -> dict:
        normalized_source_brand = self._normalize_text(source_brand)
        if not normalized_source_brand:
            raise ValidationError("Не указан бренд-источник")

        mapping = (
            self.db.query(DesignerSourceName)
            .filter(func.lower(DesignerSourceName.source_name) == normalized_source_brand.lower())
            .one_or_none()
        )
        if mapping is None:
            raise ValidationError("Бренд-источник не найден")

        mapping.is_enabled = bool(include_in_designers)
        mapping.is_admin_touched = True
        self.db.flush()
        return {
            "source_brand": normalized_source_brand,
            "include_in_designers": bool(mapping.is_enabled),
        }

    def list_taxonomy_editor_state(self) -> dict:
        filters_state = self.taxonomy.get_state()
        filter_assignment_rebuild = ProductFilterAssignmentService(self.db).get_rebuild_status()
        filter_rows = self.taxonomy.repo.list_filters()
        filter_by_slug = {str(row.slug): row for row in filter_rows}
        product_counts_by_slug = self._filter_product_counts_by_slug()
        custom_catalog_rows = self.taxonomy.repo.list_custom_catalogs()
        custom_catalog_by_slug = {str(row.slug): row for row in custom_catalog_rows}
        showcase_rows = {
            str(row.code): row
            for row in self.taxonomy.repo.list_showcase_categories()
        }

        referenced_product_ids: set[int] = set()
        for filter_node in filters_state.filters:
            stack = [filter_node]
            while stack:
                node = stack.pop()
                referenced_product_ids.update(int(product_id) for product_id in node.manual_product_ids)
                stack.extend(list(node.children or []))
        for custom_catalog in filters_state.custom_catalogs:
            referenced_product_ids.update(int(product_id) for product_id in custom_catalog.product_ids)
        product_payloads = self._admin_product_payload_index(sorted(referenced_product_ids))

        def serialize_filter_nodes(nodes: list[dict]) -> list[dict]:
            serialized: list[dict] = []
            for node in nodes:
                slug = str(node.slug or "").strip()
                entity = filter_by_slug.get(slug)
                manual_products = self._serialize_manual_products([int(item) for item in node.manual_product_ids], product_payloads)
                serialized.append(
                    {
                        "id": int(entity.id) if entity is not None else 0,
                        "slug": slug,
                        "label": str(node.title),
                        "display_label": str(node.display_title or ""),
                        "mobile_pair_root_id": (
                            int(filter_by_slug[str(node.mobile_pair_slug)].id)
                            if str(node.mobile_pair_slug or "").strip() and str(node.mobile_pair_slug) in filter_by_slug
                            else None
                        ),
                        "node_kind": str(node.node_kind),
                        "is_enabled": bool(node.is_enabled),
                        "restrict_by_gender": bool(node.restrict_by_gender),
                        "product_count": int(product_counts_by_slug.get(slug, 0)),
                        "rules": {
                            "local_category_keywords": [str(item) for item in node.local_category_keywords],
                            "title_keywords": [str(item) for item in node.title_keywords],
                            "manual_products": manual_products,
                        },
                        "children": serialize_filter_nodes(list(node.children or [])),
                    }
                )
            return serialized

        filters_payload = serialize_filter_nodes(list(filters_state.filters))
        custom_catalogs_payload = []
        for item in filters_state.custom_catalogs:
            entity = custom_catalog_by_slug.get(str(item.slug))
            custom_catalogs_payload.append(
                {
                    "id": int(entity.id) if entity is not None else 0,
                    "slug": str(item.slug),
                    "label": str(item.title),
                    "description": str(item.description or "").strip(),
                    "is_enabled": bool(item.is_enabled),
                    "manual_products": self._serialize_manual_products([int(product_id) for product_id in item.product_ids], product_payloads),
                }
            )

        filter_id_by_slug = {
            str(item["slug"]): int(item["id"])
            for item in self._flatten_filter_payload(filters_payload)
            if str(item.get("slug") or "").strip() and int(item.get("id") or 0) > 0
        }

        categories_payload = []
        for item in filters_state.showcase_categories:
            behavior, system_filter_value = self._category_behavior(str(item.code))
            entity = showcase_rows.get(str(item.code))
            attachments_payload = []
            if entity is not None:
                for attachment in sorted(entity.attachments, key=lambda value: (int(value.position), int(value.id))):
                    attachment_kind = str(attachment.attachment_kind)
                    if not self._is_showcase_attachment_allowed(item.code, attachment_kind):
                        continue
                    hidden_filter_ids = [
                        int(hidden.filter_node.filter_id)
                        for hidden in sorted(attachment.hidden_nodes, key=lambda value: int(value.filter_node_id))
                        if hidden.filter_node is not None
                    ]
                    attachments_payload.append(
                        {
                            "id": f"attachment-{int(attachment.id)}",
                            "kind": attachment_kind,
                            "ref_id": (
                                int(attachment.filter_id)
                                if attachment.filter_id is not None
                                else int(attachment.custom_catalog_id)
                            ),
                            "hidden_node_ids": hidden_filter_ids,
                        }
                    )
            else:
                for index, attachment in enumerate(item.attachments, start=1):
                    if not self._is_showcase_attachment_allowed(item.code, attachment.kind):
                        continue
                    fallback_catalog = (
                        custom_catalog_by_slug.get(str(attachment.custom_catalog_slug or "").strip())
                        if attachment.kind == "custom_catalog"
                        else None
                    )
                    attachments_payload.append(
                        {
                            "id": f"{item.code}-{index}",
                            "kind": str(attachment.kind),
                            "ref_id": (
                                int(filter_id_by_slug.get(str(attachment.filter_slug or ""), 0))
                                if attachment.kind == "filter"
                                else int(fallback_catalog.id) if fallback_catalog is not None else 0
                            ),
                            "hidden_node_ids": [
                                int(filter_id_by_slug.get(str(slug or ""), 0))
                                for slug in attachment.hidden_filter_slugs
                                if int(filter_id_by_slug.get(str(slug or ""), 0)) > 0
                            ],
                        }
                    )
            categories_payload.append(
                {
                    "id": int(entity.id) if entity is not None else 0,
                    "slug": str(item.code),
                    "label": str(item.title),
                    "behavior": behavior,
                    "system_filter_value": system_filter_value,
                    "attachments": attachments_payload,
                    "children": [],
                }
            )

        categories_payload = self._sort_category_payloads(categories_payload)

        designer_state = self.list_designer_editor_state()
        product_count_by_designer = defaultdict(int)
        for row in designer_state["rows"]:
            if not row.get("include_in_designers"):
                continue
            designer_name = self._normalize_text(row.get("designer_name"))
            if designer_name:
                product_count_by_designer[designer_name] += int(row.get("source_public_product_count") or 0)
        designer_directory = [
            {
                "id": str(item["id"]),
                "label": str(item["name"]),
                "product_count": int(product_count_by_designer.get(str(item["name"]), 0)),
            }
            for item in designer_state["designers"]
            if str(item.get("name") or "").strip() and int(product_count_by_designer.get(str(item["name"]), 0)) > 0
        ]
        designer_directory.sort(key=lambda item: (str(item["label"]), str(item["id"])))

        return {
            "filters": filters_payload,
            "categories": categories_payload,
            "custom_catalogs": custom_catalogs_payload,
            "designer_directory": designer_directory,
            "filter_assignment_rebuild": filter_assignment_rebuild,
            "hidden_product_ids": [
                int(product_id)
                for product_id, in (
                    self.db.query(Product.id)
                    .filter(Product.lifecycle_status == "active")
                    .filter(Product.visibility_status == "hidden")
                    .order_by(Product.id.asc())
                    .all()
                )
            ],
        }

    def get_filter_assignment_rebuild_status(self) -> dict:
        return ProductFilterAssignmentService(self.db).get_rebuild_status()

    def request_filter_assignment_rebuild(self) -> tuple[bool, dict]:
        return ProductFilterAssignmentService(self.db).request_full_rebuild_once()

    @staticmethod
    def _flatten_filter_payload(nodes: list[dict]) -> list[dict]:
        flattened: list[dict] = []
        for node in nodes:
            flattened.append(node)
            flattened.extend(AdminEditorService._flatten_filter_payload(list(node.get("children") or [])))
        return flattened

    def save_taxonomy_editor_state(self, payload: dict) -> dict:
        filters = payload.get("filters") if isinstance(payload.get("filters"), list) else []
        categories = payload.get("categories") if isinstance(payload.get("categories"), list) else []
        custom_catalogs = payload.get("custom_catalogs") if isinstance(payload.get("custom_catalogs"), list) else []
        hidden_product_ids = payload.get("hidden_product_ids") if isinstance(payload.get("hidden_product_ids"), list) else []

        current_catalogs = {
            int(row.id): row
            for row in self.db.query(CustomCatalog).order_by(CustomCatalog.id.asc()).all()
        }
        current_filters = {
            int(row.id): row
            for row in self.taxonomy.repo.list_filters()
        }
        current_showcase_categories = {
            int(row.id): row
            for row in self.taxonomy.repo.list_showcase_categories()
        }
        filter_ref_slug_by_id: dict[int, str] = {}
        custom_catalog_ref_slug_by_id: dict[int, str] = {}
        used_temp_filter_refs: set[str] = set()
        used_temp_catalog_refs: set[str] = set()

        def next_temp_ref(prefix: str, node_id: int, label: object, used: set[str]) -> str:
            base = TaxonomyService._slugify(self._normalize_text(label) or prefix)
            candidate = f"draft-{prefix}-{node_id or len(used) + 1}-{base}".strip("-")
            if candidate not in used:
                used.add(candidate)
                return candidate
            index = 2
            while f"{candidate}-{index}" in used:
                index += 1
            final = f"{candidate}-{index}"
            used.add(final)
            return final

        def build_filter_node(node: dict) -> dict:
            children = node.get("children") if isinstance(node.get("children"), list) else []
            rules = node.get("rules") if isinstance(node.get("rules"), dict) else {}
            manual_products = rules.get("manual_products") if isinstance(rules.get("manual_products"), list) else []
            local_keywords = rules.get("local_category_keywords") if isinstance(rules.get("local_category_keywords"), list) else []
            title_keywords = rules.get("title_keywords") if isinstance(rules.get("title_keywords"), list) else []
            mobile_pair_root_id = int(node.get("mobile_pair_root_id") or 0)
            node_id = int(node.get("id") or 0)
            persisted_filter = current_filters.get(node_id)
            ref_slug = (
                str(persisted_filter.slug).strip()
                if persisted_filter is not None and str(getattr(persisted_filter, "slug", "")).strip()
                else next_temp_ref("filter", node_id, node.get("label"), used_temp_filter_refs)
            )
            if node_id > 0:
                filter_ref_slug_by_id[node_id] = ref_slug
            return {
                "ref_slug": ref_slug,
                "title": self._normalize_text(node.get("label")) or "Без названия",
                "display_title": self._normalize_text(node.get("display_label")) or None,
                "mobile_pair_slug": None,
                "node_kind": (
                    "multifilter"
                    if children
                    else (
                        str(node.get("node_kind") or "").strip()
                        if str(node.get("node_kind") or "").strip() in {"filter", "multifilter"}
                        else "filter"
                    )
                ),
                "is_enabled": bool(node.get("is_enabled", True)),
                "restrict_by_gender": bool(node.get("restrict_by_gender", True)),
                "local_category_keywords": [self._normalize_text(item) for item in local_keywords if self._normalize_text(item)],
                "title_keywords": [self._normalize_text(item) for item in title_keywords if self._normalize_text(item)],
                "manual_product_ids": [int(item.get("product_id")) for item in manual_products if int(item.get("product_id") or 0) > 0],
                "children": [build_filter_node(child) for child in children if isinstance(child, dict)],
                "_mobile_pair_root_id": mobile_pair_root_id if mobile_pair_root_id > 0 else None,
            }

        def apply_mobile_pair_refs(nodes: list[dict]) -> list[dict]:
            normalized: list[dict] = []
            for node in nodes:
                raw_pair_root_id = int(node.pop("_mobile_pair_root_id") or 0)
                normalized_node = dict(node)
                normalized_node["mobile_pair_slug"] = filter_ref_slug_by_id.get(raw_pair_root_id) if raw_pair_root_id > 0 else None
                normalized_node["children"] = apply_mobile_pair_refs(list(node.get("children") or []))
                normalized.append(normalized_node)
            return normalized

        filter_payload_nodes = apply_mobile_pair_refs([build_filter_node(node) for node in filters if isinstance(node, dict)])

        custom_catalog_payloads: list[dict] = []
        for catalog in custom_catalogs:
            if not isinstance(catalog, dict):
                continue
            catalog_id = int(catalog.get("id") or 0)
            product_ids = [
                int(item.get("product_id"))
                for item in (catalog.get("manual_products") if isinstance(catalog.get("manual_products"), list) else [])
                if int(item.get("product_id") or 0) > 0
            ]
            existing = current_catalogs.get(catalog_id)
            ref_slug = (
                str(existing.slug).strip()
                if existing is not None and str(getattr(existing, "slug", "")).strip()
                else next_temp_ref("catalog", catalog_id, catalog.get("label"), used_temp_catalog_refs)
            )
            custom_catalog_ref_slug_by_id[catalog_id] = ref_slug
            custom_catalog_payloads.append(
                {
                    "ref_slug": ref_slug,
                    "title": self._normalize_text(catalog.get("label")) or "Без названия",
                    "description": self._normalize_text(catalog.get("description")) or None,
                    "is_enabled": bool(catalog.get("is_enabled", True)),
                    "product_ids": product_ids,
                }
            )

        showcase_payloads: list[dict] = []
        for category in categories:
            if not isinstance(category, dict):
                continue
            category_id = int(category.get("id") or 0)
            category_entity = current_showcase_categories.get(category_id)
            category_code = (
                str(category_entity.code).strip()
                if category_entity is not None and str(getattr(category_entity, "code", "")).strip()
                else str(category.get("slug") or category.get("behavior") or "").strip()
            )
            attachments_payload: list[dict] = []
            for attachment in (category.get("attachments") if isinstance(category.get("attachments"), list) else []):
                if not isinstance(attachment, dict):
                    continue
                kind = str(attachment.get("kind") or "").strip()
                ref_id = int(attachment.get("ref_id") or 0)
                if not self._is_showcase_attachment_allowed(category_code, kind):
                    continue
                if kind == "filter":
                    filter_ref_slug = filter_ref_slug_by_id.get(ref_id)
                    if not filter_ref_slug:
                        continue
                    hidden_slugs = [
                        ref_slug
                        for hidden_id in (attachment.get("hidden_node_ids") if isinstance(attachment.get("hidden_node_ids"), list) else [])
                        if (ref_slug := filter_ref_slug_by_id.get(int(hidden_id or 0)))
                    ]
                    attachments_payload.append(
                        {
                            "kind": "filter",
                            "filter_slug": filter_ref_slug,
                            "custom_catalog_slug": None,
                            "hidden_filter_slugs": hidden_slugs,
                        }
                    )
                elif kind == "custom_catalog":
                    catalog_ref_slug = custom_catalog_ref_slug_by_id.get(ref_id)
                    if not catalog_ref_slug:
                        continue
                    attachments_payload.append(
                        {
                            "kind": "custom_catalog",
                            "filter_slug": None,
                            "custom_catalog_slug": catalog_ref_slug,
                            "hidden_filter_slugs": [],
                        }
                    )
            showcase_payloads.append(
                {
                    "code": category_code,
                    "title": self._normalize_text(category.get("label")) or "Без названия",
                    "attachments": attachments_payload,
                }
            )

        taxonomy_payload = {
            "filters": filter_payload_nodes,
            "custom_catalogs": custom_catalog_payloads,
            "showcase_categories": showcase_payloads,
        }
        self.taxonomy.replace_state_from_write(TaxonomyWriteState.model_validate(taxonomy_payload))

        normalized_hidden_ids = sorted({int(product_id) for product_id in hidden_product_ids if int(product_id or 0) > 0})
        requested_hidden_set = set(normalized_hidden_ids)
        current_hidden_ids = {
            int(product_id)
            for product_id, in (
                self.db.query(Product.id)
                .filter(Product.lifecycle_status == "active")
                .filter(Product.visibility_status == "hidden")
                .all()
            )
        }
        to_hide = [product_id for product_id in normalized_hidden_ids if product_id not in current_hidden_ids]
        to_show = [product_id for product_id in current_hidden_ids if product_id not in requested_hidden_set]

        if to_show:
            (
                self.db.query(Product)
                .filter(Product.id.in_(to_show))
                .update({Product.visibility_status: "visible"}, synchronize_session=False)
            )
        if to_hide:
            (
                self.db.query(Product)
                .filter(Product.id.in_(to_hide))
                .update({Product.visibility_status: "hidden"}, synchronize_session=False)
            )
        self.db.flush()
        return self.list_taxonomy_editor_state()
