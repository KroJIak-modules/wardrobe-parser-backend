from typing import Optional

from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.models.product import Product


class ProductRepository:
    @staticmethod
    def get_by_id(db: Session, product_id: int, include_deleted: bool = False) -> Optional[Product]:
        query = db.query(Product).filter(Product.id == product_id)
        if not include_deleted:
            query = query.filter(Product.is_deleted == False)
        return query.first()

    @staticmethod
    def get_by_external_id(
        db: Session,
        site_id: int,
        external_id: str,
        include_deleted: bool = False,
    ) -> Optional[Product]:
        query = db.query(Product).filter(
            Product.site_id == site_id,
            Product.external_id == external_id,
        )
        if not include_deleted:
            query = query.filter(Product.is_deleted == False)
        return query.first()

    @staticmethod
    def list_by_site(
        db: Session,
        site_id: int,
        cursor_id: int | None,
        limit: int,
        filter_key: str | None,
        filter_value: str | None,
    ) -> tuple[list[Product], int | None]:
        query = db.query(Product).filter(
            Product.site_id == site_id,
            Product.is_deleted == False,
        )
        if cursor_id is not None:
            query = query.filter(Product.id > cursor_id)
        allowed_filters = {"category", "currency", "name", "external_id"}
        if filter_key and filter_value and filter_key in allowed_filters:
            column = getattr(Product, filter_key, None)
            if column is not None:
                query = query.filter(column == filter_value)
        items = query.order_by(Product.id.asc()).limit(limit + 1).all()
        next_cursor = None
        if len(items) > limit:
            next_cursor = items[-1].id
            items = items[:-1]
        return items, next_cursor

    @staticmethod
    def create(db: Session, **kwargs) -> Product:
        product = Product(**kwargs)
        db.add(product)
        db.flush()
        db.refresh(product)
        return product

    @staticmethod
    def update(db: Session, product: Product, **kwargs) -> Product:
        for key, value in kwargs.items():
            if hasattr(product, key):
                setattr(product, key, value)
        db.flush()
        db.refresh(product)
        return product

    @staticmethod
    def soft_delete(db: Session, product: Product) -> None:
        product.is_deleted = True
        product.deleted_at = utcnow()
        db.flush()
