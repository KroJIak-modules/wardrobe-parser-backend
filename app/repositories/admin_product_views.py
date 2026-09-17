"""Data access for per-admin product view tracking."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import AdminProductView


class AdminProductViewRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def filter_viewed_product_ids(self, *, admin_user_id: int, product_ids: Iterable[int]) -> set[int]:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not normalized_ids:
            return set()
        rows = (
            self.session.query(AdminProductView.product_id)
            .filter(AdminProductView.admin_user_id == int(admin_user_id))
            .filter(AdminProductView.product_id.in_(normalized_ids))
            .all()
        )
        return {int(row[0]) for row in rows}

    def mark_products_viewed(self, *, admin_user_id: int, product_ids: Iterable[int]) -> None:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not normalized_ids:
            return
        self.session.execute(
            insert(AdminProductView)
            .values([{"admin_user_id": int(admin_user_id), "product_id": product_id} for product_id in normalized_ids])
            .on_conflict_do_nothing()
        )

    def mark_products_viewed_from_scope(self, *, admin_user_id: int, product_ids_scope) -> int:
        """Bulk-mark every product of the given id subquery in a single statement."""
        statement = (
            insert(AdminProductView)
            .from_select(
                ["admin_user_id", "product_id"],
                select(
                    literal(int(admin_user_id)).label("admin_user_id"),
                    product_ids_scope.c.product_id.label("product_id"),
                ),
            )
            .on_conflict_do_nothing()
        )
        result = self.session.execute(statement)
        return int(getattr(result, "rowcount", 0) or 0)
