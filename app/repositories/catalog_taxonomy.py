"""Repository for catalog taxonomy tables."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import case
from sqlalchemy.orm import Session, selectinload

from app.models import (
    CustomCatalog,
    CustomCatalogProduct,
    Filter,
    FilterNode,
    Product,
    ShowcaseCategoryAttachmentHiddenNode,
    ShowcaseCategory,
    ShowcaseCategoryAttachment,
)

SHOWCASE_CATEGORY_ORDER = ("new", "designers", "men", "women", "sale")
SHOWCASE_CATEGORY_ORDER_INDEX = {
    code: index
    for index, code in enumerate(SHOWCASE_CATEGORY_ORDER, start=1)
}


class CatalogTaxonomyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_filters(self) -> list[Filter]:
        return (
            self.session.query(Filter)
            .options(
                selectinload(Filter.node),
                selectinload(Filter.default_weight_rule),
                selectinload(Filter.local_category_keywords),
                selectinload(Filter.title_keywords),
                selectinload(Filter.manual_products),
            )
            .order_by(Filter.id.asc())
            .all()
        )

    def list_filter_nodes(self) -> list[FilterNode]:
        return (
            self.session.query(FilterNode)
            .order_by(FilterNode.parent_node_id.asc().nullsfirst(), FilterNode.position.asc(), FilterNode.id.asc())
            .all()
        )

    def list_custom_catalogs(self) -> list[CustomCatalog]:
        return (
            self.session.query(CustomCatalog)
            .options(selectinload(CustomCatalog.products))
            .order_by(CustomCatalog.slug.asc(), CustomCatalog.id.asc())
            .all()
        )

    def list_custom_catalog_membership(self, product_id: int) -> list[tuple[str, str, bool]]:
        rows = (
            self.session.query(
                CustomCatalog.slug,
                CustomCatalog.title,
                CustomCatalogProduct.product_id,
            )
            .outerjoin(
                CustomCatalogProduct,
                (CustomCatalogProduct.catalog_id == CustomCatalog.id)
                & (CustomCatalogProduct.product_id == int(product_id)),
            )
            .order_by(CustomCatalog.title.asc(), CustomCatalog.id.asc())
            .all()
        )
        return [
            (str(slug), str(title), assigned_product_id is not None)
            for slug, title, assigned_product_id in rows
        ]

    def get_custom_catalog_by_slug(self, slug: str) -> CustomCatalog | None:
        return self.session.query(CustomCatalog).filter(CustomCatalog.slug == slug).one_or_none()

    def set_custom_catalog_product_membership(self, *, catalog_id: int, product_id: int, is_assigned: bool) -> None:
        if is_assigned:
            existing = (
                self.session.query(CustomCatalogProduct)
                .filter(
                    CustomCatalogProduct.catalog_id == int(catalog_id),
                    CustomCatalogProduct.product_id == int(product_id),
                )
                .one_or_none()
            )
            if existing is None:
                self.session.add(CustomCatalogProduct(catalog_id=int(catalog_id), product_id=int(product_id)))
        else:
            (
                self.session.query(CustomCatalogProduct)
                .filter(
                    CustomCatalogProduct.catalog_id == int(catalog_id),
                    CustomCatalogProduct.product_id == int(product_id),
                )
                .delete(synchronize_session=False)
            )
        self.session.flush()

    def list_showcase_categories(self) -> list[ShowcaseCategory]:
        return (
            self.session.query(ShowcaseCategory)
            .options(
                selectinload(ShowcaseCategory.attachments).selectinload(ShowcaseCategoryAttachment.hidden_nodes),
                selectinload(ShowcaseCategory.attachments).selectinload(ShowcaseCategoryAttachment.filter),
                selectinload(ShowcaseCategory.attachments).selectinload(ShowcaseCategoryAttachment.custom_catalog),
            )
            .order_by(
                case(
                    SHOWCASE_CATEGORY_ORDER_INDEX,
                    value=ShowcaseCategory.code,
                    else_=len(SHOWCASE_CATEGORY_ORDER_INDEX) + 1,
                ),
                ShowcaseCategory.id.asc(),
            )
            .all()
        )

    def get_existing_product_ids(self, product_ids: set[int]) -> set[int]:
        if not product_ids:
            return set()
        rows = (
            self.session.query(Product.id)
            .filter(Product.id.in_(list(product_ids)))
            .filter(Product.lifecycle_status == "active")
            .all()
        )
        return {int(row.id) for row in rows}

    def clear_editable_state(self) -> None:
        for model in (
            ShowcaseCategoryAttachmentHiddenNode,
            ShowcaseCategoryAttachment,
            CustomCatalog,
            FilterNode,
            Filter,
        ):
            self.session.query(model).delete(synchronize_session=False)

    def add(self, entity) -> None:
        self.session.add(entity)

    def add_all(self, entities: Iterable[object]) -> None:
        self.session.add_all(list(entities))

    def flush(self) -> None:
        self.session.flush()
