"""Service for the new catalog taxonomy state."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import (
    CustomCatalog,
    CustomCatalogProduct,
    Filter,
    FilterLocalCategoryKeyword,
    FilterManualProduct,
    FilterNode,
    FilterTitleKeyword,
    ShowcaseCategory,
    ShowcaseCategoryAttachment,
    ShowcaseCategoryAttachmentHiddenNode,
)
from app.repositories.catalog_taxonomy import CatalogTaxonomyRepository
from app.schemas.taxonomy import (
    TaxonomyCustomCatalog,
    TaxonomyFilterNode,
    TaxonomyShowcaseAttachment,
    TaxonomyShowcaseCategory,
    TaxonomyState,
)


class TaxonomyService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = CatalogTaxonomyRepository(db)

    @staticmethod
    def _clean_text_list(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw_value in values:
            value = str(raw_value or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    @staticmethod
    def _clean_int_list(values: list[int]) -> list[int]:
        result: list[int] = []
        seen: set[int] = set()
        for raw_value in values:
            value = int(raw_value)
            if value <= 0 or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _serialize_filter_tree(self) -> list[TaxonomyFilterNode]:
        filters = self.repo.list_filters()
        nodes = self.repo.list_filter_nodes()
        filters_by_id = {int(item.id): item for item in filters}
        node_by_filter_id = {int(item.filter_id): item for item in nodes}
        children_by_parent: dict[int | None, list[FilterNode]] = {}
        for node in nodes:
            parent_id = int(node.parent_node_id) if node.parent_node_id is not None else None
            children_by_parent.setdefault(parent_id, []).append(node)
        for bucket in children_by_parent.values():
            bucket.sort(key=lambda item: (int(item.position), int(item.id)))

        def build(node: FilterNode) -> TaxonomyFilterNode:
            entity = filters_by_id[int(node.filter_id)]
            return TaxonomyFilterNode(
                slug=str(entity.slug),
                title=str(entity.title),
                display_title=(str(entity.display_title) if entity.display_title else None),
                node_kind=str(entity.node_kind),
                is_enabled=bool(entity.is_enabled),
                local_category_keywords=[str(item.keyword) for item in sorted(entity.local_category_keywords, key=lambda value: int(value.id))],
                title_keywords=[str(item.keyword) for item in sorted(entity.title_keywords, key=lambda value: int(value.id))],
                manual_product_ids=[int(item.product_id) for item in sorted(entity.manual_products, key=lambda value: int(value.product_id))],
                children=[build(child) for child in children_by_parent.get(int(node.id), [])],
            )

        root_nodes = children_by_parent.get(None, [])
        orphan_roots = [
            node_by_filter_id[filter_id]
            for filter_id in sorted(node_by_filter_id.keys())
            if node_by_filter_id[filter_id].parent_node_id is None and node_by_filter_id[filter_id] not in root_nodes
        ]
        return [build(node) for node in [*root_nodes, *orphan_roots]]

    def get_state(self) -> TaxonomyState:
        nodes = self.repo.list_filter_nodes()
        filters = self.repo.list_filters()
        filter_slug_by_node_id = {
            int(node.id): str(next(item.slug for item in filters if int(item.id) == int(node.filter_id)))
            for node in nodes
        }
        showcase_categories: list[TaxonomyShowcaseCategory] = []
        for category in self.repo.list_showcase_categories():
            attachments: list[TaxonomyShowcaseAttachment] = []
            for attachment in sorted(category.attachments, key=lambda item: (int(item.position), int(item.id))):
                attachments.append(
                    TaxonomyShowcaseAttachment(
                        kind=str(attachment.attachment_kind),
                        filter_slug=(str(attachment.filter.slug) if attachment.filter is not None else None),
                        custom_catalog_slug=(str(attachment.custom_catalog.slug) if attachment.custom_catalog is not None else None),
                        hidden_filter_slugs=[
                            filter_slug_by_node_id[int(hidden.filter_node_id)]
                            for hidden in sorted(attachment.hidden_nodes, key=lambda item: int(item.filter_node_id))
                            if int(hidden.filter_node_id) in filter_slug_by_node_id
                        ],
                    )
                )
            showcase_categories.append(
                TaxonomyShowcaseCategory(
                    code=str(category.code),
                    title=str(category.title),
                    attachments=attachments,
                )
            )

        custom_catalogs = [
            TaxonomyCustomCatalog(
                slug=str(catalog.slug),
                title=str(catalog.title),
                description=(str(catalog.description) if catalog.description else None),
                is_enabled=bool(catalog.is_enabled),
                product_ids=[int(item.product_id) for item in sorted(catalog.products, key=lambda value: int(value.product_id))],
            )
            for catalog in self.repo.list_custom_catalogs()
        ]

        return TaxonomyState(
            filters=self._serialize_filter_tree(),
            custom_catalogs=custom_catalogs,
            showcase_categories=showcase_categories,
        )

    def _validate_state(self, payload: TaxonomyState) -> tuple[dict[str, TaxonomyFilterNode], dict[str, TaxonomyCustomCatalog]]:
        filters_by_slug: dict[str, TaxonomyFilterNode] = {}

        def walk(nodes: list[TaxonomyFilterNode]) -> None:
            for node in nodes:
                slug = str(node.slug).strip()
                if slug in filters_by_slug:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Duplicate filter slug: {slug}")
                filters_by_slug[slug] = node
                walk(node.children)

        walk(payload.filters)

        catalogs_by_slug: dict[str, TaxonomyCustomCatalog] = {}
        for catalog in payload.custom_catalogs:
            slug = str(catalog.slug).strip()
            if slug in catalogs_by_slug:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Duplicate custom catalog slug: {slug}")
            catalogs_by_slug[slug] = catalog

        category_codes: set[str] = set()
        for showcase_category in payload.showcase_categories:
            code = str(showcase_category.code).strip()
            if code in category_codes:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Duplicate showcase category code: {code}")
            category_codes.add(code)
            for attachment in showcase_category.attachments:
                if attachment.kind == "filter":
                    if not attachment.filter_slug or attachment.custom_catalog_slug:
                        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid filter attachment in showcase category: {code}")
                    if attachment.filter_slug not in filters_by_slug:
                        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown filter slug in attachment: {attachment.filter_slug}")
                else:
                    if not attachment.custom_catalog_slug or attachment.filter_slug:
                        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid custom catalog attachment in showcase category: {code}")
                    if attachment.custom_catalog_slug not in catalogs_by_slug:
                        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown custom catalog slug in attachment: {attachment.custom_catalog_slug}")
                for hidden_slug in self._clean_text_list(attachment.hidden_filter_slugs):
                    if hidden_slug not in filters_by_slug:
                        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown hidden filter slug: {hidden_slug}")

        product_ids: set[int] = set()
        for node in filters_by_slug.values():
            product_ids.update(self._clean_int_list(node.manual_product_ids))
        for catalog in catalogs_by_slug.values():
            product_ids.update(self._clean_int_list(catalog.product_ids))
        existing_product_ids = self.repo.get_existing_product_ids(product_ids)
        missing_product_ids = sorted(product_ids - existing_product_ids)
        if missing_product_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown or inactive product ids: {', '.join(str(item) for item in missing_product_ids)}",
            )

        return filters_by_slug, catalogs_by_slug

    def replace_state(self, payload: TaxonomyState) -> TaxonomyState:
        filters_by_slug, catalogs_by_slug = self._validate_state(payload)
        try:
            self.repo.clear_state()
            self.repo.flush()

            filter_entity_by_slug: dict[str, Filter] = {}
            filter_node_by_slug: dict[str, FilterNode] = {}

            def create_filter_nodes(nodes: list[TaxonomyFilterNode], parent_node: FilterNode | None) -> None:
                for position, node in enumerate(nodes, start=1):
                    entity = Filter(
                        title=str(node.title).strip(),
                        display_title=(str(node.display_title).strip() if node.display_title else None),
                        slug=str(node.slug).strip(),
                        node_kind=str(node.node_kind).strip() or "filter",
                        is_enabled=bool(node.is_enabled),
                    )
                    self.repo.add(entity)
                    self.repo.flush()
                    filter_entity_by_slug[entity.slug] = entity

                    filter_node = FilterNode(
                        filter_id=int(entity.id),
                        parent_node_id=(int(parent_node.id) if parent_node is not None else None),
                        position=position,
                    )
                    self.repo.add(filter_node)
                    self.repo.flush()
                    filter_node_by_slug[entity.slug] = filter_node

                    self.repo.add_all(
                        FilterLocalCategoryKeyword(filter_id=int(entity.id), keyword=keyword)
                        for keyword in self._clean_text_list(node.local_category_keywords)
                    )
                    self.repo.add_all(
                        FilterTitleKeyword(filter_id=int(entity.id), keyword=keyword)
                        for keyword in self._clean_text_list(node.title_keywords)
                    )
                    self.repo.add_all(
                        FilterManualProduct(filter_id=int(entity.id), product_id=product_id)
                        for product_id in self._clean_int_list(node.manual_product_ids)
                    )
                    create_filter_nodes(node.children, filter_node)

            create_filter_nodes(payload.filters, parent_node=None)

            custom_catalog_entity_by_slug: dict[str, CustomCatalog] = {}
            for catalog in payload.custom_catalogs:
                entity = CustomCatalog(
                    title=str(catalog.title).strip(),
                    description=(str(catalog.description).strip() if catalog.description else None),
                    slug=str(catalog.slug).strip(),
                    is_enabled=bool(catalog.is_enabled),
                )
                self.repo.add(entity)
                self.repo.flush()
                custom_catalog_entity_by_slug[entity.slug] = entity
                self.repo.add_all(
                    CustomCatalogProduct(catalog_id=int(entity.id), product_id=product_id)
                    for product_id in self._clean_int_list(catalog.product_ids)
                )

            for showcase_category in payload.showcase_categories:
                category_entity = ShowcaseCategory(code=str(showcase_category.code).strip(), title=str(showcase_category.title).strip())
                self.repo.add(category_entity)
                self.repo.flush()
                for position, attachment in enumerate(showcase_category.attachments, start=1):
                    attachment_entity = ShowcaseCategoryAttachment(
                        showcase_category_id=int(category_entity.id),
                        attachment_kind=str(attachment.kind),
                        filter_id=(
                            int(filter_entity_by_slug[str(attachment.filter_slug)].id)
                            if attachment.kind == "filter" and attachment.filter_slug
                            else None
                        ),
                        custom_catalog_id=(
                            int(custom_catalog_entity_by_slug[str(attachment.custom_catalog_slug)].id)
                            if attachment.kind == "custom_catalog" and attachment.custom_catalog_slug
                            else None
                        ),
                        position=position,
                    )
                    self.repo.add(attachment_entity)
                    self.repo.flush()
                    self.repo.add_all(
                        ShowcaseCategoryAttachmentHiddenNode(
                            attachment_id=int(attachment_entity.id),
                            filter_node_id=int(filter_node_by_slug[hidden_slug].id),
                        )
                        for hidden_slug in self._clean_text_list(attachment.hidden_filter_slugs)
                    )

            self.db.commit()
        except HTTPException:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            raise
        return self.get_state()
