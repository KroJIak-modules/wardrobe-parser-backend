from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models.product import Product
from app.repositories.product_repository import ProductRepository
from app.repositories.site_repository import SiteRepository


class ProductService:
    @staticmethod
    def upsert_from_parser(db: Session, items: list[dict]) -> tuple[int, int]:
        created = 0
        updated = 0
        now = datetime.now(timezone.utc)
        try:
            for item in items:
                site_key = item.get("site_key")
                if not site_key:
                    raise ValidationError("site_key is required")
                site = SiteRepository.get_by_key(db, site_key)
                if site is None:
                    site = SiteRepository.create(
                        db,
                        key=site_key,
                        name=item.get("site_name", site_key),
                        base_url=item.get("site_base_url"),
                    )
                external_id = item.get("external_id") or item.get("product_url")
                if not external_id:
                    raise ValidationError("external_id or product_url is required")
                existing = ProductRepository.get_by_external_id(db, site.id, external_id)
                payload = {
                    "site_id": site.id,
                    "external_id": external_id,
                    "name": item.get("name", ""),
                    "category": item.get("category"),
                    "price": item.get("price"),
                    "currency": item.get("currency"),
                    "product_url": item.get("product_url") or external_id,
                    "image_url": item.get("image_url"),
                    "description": item.get("description"),
                    "raw_data": item.get("raw_data"),
                    "parser_updated_at": now,
                }
                if existing is None:
                    ProductRepository.create(db, **payload)
                    created += 1
                    continue
                if existing.user_updated_at is not None:
                    continue
                ProductRepository.update(db, existing, **payload)
                updated += 1
            db.commit()
        except Exception:
            db.rollback()
            raise
        return created, updated

    @staticmethod
    def mark_user_update(db: Session, product: Product) -> None:
        product.user_updated_at = datetime.now(timezone.utc)
        db.flush()
