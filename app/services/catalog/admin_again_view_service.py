from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models import Designer, Product
from app.repositories.catalog_again import AdminAgainViewRepository
from app.services.catalog.designer_support import normalize_designer_text


class AdminAgainViewService:
    def __init__(self, db: Session, admin_user_id: int) -> None:
        self.db = db
        self.admin_user_id = int(admin_user_id)
        self.views = AdminAgainViewRepository(db)

    def mark_product_viewed(self, product_id: int) -> None:
        exists = self.db.query(Product.id).filter(Product.id == int(product_id)).one_or_none()
        if exists is None:
            raise NotFoundError("Товар не найден")
        self.views.mark_viewed(
            admin_user_id=self.admin_user_id,
            entity_kind="product",
            entity_key=str(int(product_id)),
        )
        self.db.commit()

    def mark_brand_viewed(self, source_brand: str) -> None:
        key = normalize_designer_text(source_brand)
        if not key:
            raise NotFoundError("Бренд-источник не найден")
        self.views.mark_viewed(
            admin_user_id=self.admin_user_id,
            entity_kind="brand",
            entity_key=key,
        )
        self.db.commit()

    def mark_designer_viewed(self, designer_id: int) -> None:
        designer = self.db.query(Designer).filter(Designer.id == int(designer_id)).one_or_none()
        if designer is None:
            raise NotFoundError("Дизайнер не найден")
        self.views.mark_viewed(
            admin_user_id=self.admin_user_id,
            entity_kind="designer",
            entity_key=normalize_designer_text(designer.name),
        )
        self.db.commit()
