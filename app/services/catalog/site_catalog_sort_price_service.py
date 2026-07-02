from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.repositories.catalog_products import CatalogProductRepository
from app.services.catalog.product_query_service import ProductQueryService
from app.services.catalog.site_catalog_sort_price_queue import SiteCatalogSortPriceQueue


class SiteCatalogSortPriceService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = CatalogProductRepository(db)
        self.queue = SiteCatalogSortPriceQueue()
        self.query = ProductQueryService(db)

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _normalize_price(value: float | int | None) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def refresh_product_ids(
        self,
        product_ids: set[int] | list[int] | tuple[int, ...],
        *,
        commit: bool = True,
    ) -> int:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not normalized_ids:
            return 0

        products = self.products.list_products_for_site_price_sort_by_ids(normalized_ids)
        synced_at = self._now_utc()
        for product in products:
            variants = self.query._build_variants(product)
            price_summary = self.query._build_price_summary(variants)
            final_display_price = (
                float(price_summary.get("final_display_price"))
                if isinstance(price_summary, dict) and price_summary.get("final_display_price") is not None
                else None
            )
            product.site_sort_price_rub = self._normalize_price(final_display_price)
            product.site_sort_price_synced_at = synced_at
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return len(products)

    def refresh_missing_batch(self, *, batch_size: int) -> int:
        product_ids = self.products.list_missing_site_sort_price_product_ids(limit=batch_size)
        if not product_ids:
            return 0
        return self.refresh_product_ids(product_ids)

    def enqueue_product_ids_after_commit(self, product_ids: set[int] | list[int] | tuple[int, ...]) -> None:
        self.queue.enqueue_product_ids_after_commit(self.db, product_ids)

    def enqueue_product_ids(self, product_ids: set[int] | list[int] | tuple[int, ...]) -> int:
        return self.queue.enqueue_product_ids(product_ids)

    def enqueue_all_active_products(self) -> int:
        page_size = 5000
        offset = 0
        enqueued = 0
        while True:
            batch = self.products.list_active_product_ids_page(limit=page_size, offset=offset)
            if not batch:
                break
            enqueued += self.queue.enqueue_product_ids(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return enqueued

    def enqueue_all_active_products_after_commit(self) -> None:
        page_size = 5000
        offset = 0
        collected: list[int] = []
        while True:
            batch = self.products.list_active_product_ids_page(limit=page_size, offset=offset)
            if not batch:
                break
            collected.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        self.enqueue_product_ids_after_commit(collected)

    def enqueue_source_product_ids_after_commit(self, source_ids: set[int] | list[int] | tuple[int, ...]) -> None:
        product_ids = self.products.list_active_product_ids_by_source_ids(source_ids)
        self.enqueue_product_ids_after_commit(product_ids)

    def enqueue_source_product_ids(self, source_ids: set[int] | list[int] | tuple[int, ...]) -> int:
        product_ids = self.products.list_active_product_ids_by_source_ids(source_ids)
        return self.enqueue_product_ids(product_ids)
