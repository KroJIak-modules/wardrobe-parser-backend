from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models.product import Product
from app.repositories.product_repository import ProductRepository
from app.repositories.site_repository import SiteRepository
from app.repositories.product_image_repository import ProductImageRepository


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
                    "size": item.get("size"),
                    "additional_info": item.get("additional_info"),
                    "size_data": item.get("size_data"),
                    "product_url": item.get("product_url") or external_id,
                    "image_url": item.get("image_url"),
                    "description": item.get("description"),
                    "parser_updated_at": now,
                }
                image_urls = ProductService._extract_image_urls(item)
                if existing is None:
                    created_product = ProductRepository.create(db, **payload)
                    if image_urls:
                        ProductImageRepository.replace_images(db, created_product.id, image_urls)
                    created += 1
                    continue
                if existing.user_updated_at is not None:
                    continue
                ProductRepository.update(db, existing, **payload)
                if image_urls:
                    ProductImageRepository.replace_images(db, existing.id, image_urls)
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

    @staticmethod
    def _extract_image_urls(item: dict) -> list[str]:
        images = item.get("image_urls")
        if isinstance(images, list):
            return [str(url) for url in images if url]
        fallback = item.get("image_url")
        return [fallback] if fallback else []
