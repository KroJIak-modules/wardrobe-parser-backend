from __future__ import annotations

from app.core.database import SessionLocal
from app.core.exceptions import ValidationError
from app.models import Product
from app.services.catalog.product_query_service import ProductQueryService
from app.services.catalog.product_write_service import ProductWriteService


def test_sync_bound_create_does_not_leave_orphan_product_after_failure_and_later_commit() -> None:
    db = SessionLocal()
    try:
        start_max_id = int(db.query(Product.id).order_by(Product.id.desc()).first()[0] or 0) if db.query(Product.id).first() else 0

        try:
            ProductWriteService(db).create_sync_bound_product(
                payload={
                    "title": "Broken sync create",
                    "gender": "unisex",
                    "availability_mode": "by_order",
                    "visibility_status": "visible",
                },
                source_id=1,
                service_item={},
                force_primary_listing=False,
            )
            raise AssertionError("Expected ValidationError")
        except ValidationError as exc:
            assert "основного listing" in str(exc)

        db.commit()

        leaked = (
            db.query(Product)
            .filter(Product.id > int(start_max_id))
            .order_by(Product.id.asc())
            .all()
        )
        assert leaked == []
    finally:
        db.rollback()
        db.close()


def test_admin_products_table_ignores_products_without_primary_listing() -> None:
    db = SessionLocal()
    try:
        orphan = Product(
            gender="unisex",
            source_gender="unisex",
            gender_is_manual=False,
            availability_mode="by_order",
            lifecycle_status="active",
            visibility_status="visible",
        )
        db.add(orphan)
        db.flush()

        payload = ProductQueryService(db).list_admin_table_products(limit=200, offset=0)
        listed_ids = {int(item["id"]) for item in payload["items"]}

        assert int(orphan.id) not in listed_ids
    finally:
        db.rollback()
        db.close()
