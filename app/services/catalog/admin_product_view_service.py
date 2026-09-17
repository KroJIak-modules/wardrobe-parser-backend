"""Per-admin "new product" tracking for the control panel."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models import Product
from app.repositories.admin_product_views import AdminProductViewRepository
from app.services.catalog.product_query_service import ProductQueryService


class AdminProductViewService:
    def __init__(self, db: Session, admin_user_id: int) -> None:
        self.db = db
        self.admin_user_id = int(admin_user_id)
        self.views = AdminProductViewRepository(db)

    def apply_new_flags_to_table_payload(self, payload: dict) -> dict:
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            return payload
        product_ids = [
            item["id"]
            for item in items
            if isinstance(item, dict) and isinstance(item.get("id"), int)
        ]
        viewed_ids = self.views.filter_viewed_product_ids(
            admin_user_id=self.admin_user_id,
            product_ids=product_ids,
        )
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("id"), int):
                item["is_new"] = item["id"] not in viewed_ids
        return payload

    def mark_products_viewed(self, product_ids: Iterable[int]) -> None:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not normalized_ids:
            return
        existing_ids = {
            int(row[0])
            for row in self.db.query(Product.id).filter(Product.id.in_(normalized_ids)).all()
        }
        if not existing_ids:
            raise NotFoundError("Товар не найден")
        self.views.mark_products_viewed(admin_user_id=self.admin_user_id, product_ids=existing_ids)
        self.db.commit()

    def mark_all_products_viewed(self, **filters) -> int:
        """Mark every product matching the admin table filters as viewed in one statement."""
        scope = ProductQueryService(self.db).filtered_product_ids_subquery(
            admin_user_id=self.admin_user_id,
            alias="mark_all_viewed_scope",
            **filters,
        )
        marked = self.views.mark_products_viewed_from_scope(
            admin_user_id=self.admin_user_id,
            product_ids_scope=scope,
        )
        self.db.commit()
        return marked
