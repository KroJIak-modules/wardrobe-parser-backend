"""Per-admin NEW marks for the designer editor (source brands and catalog designers)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models import Designer, DesignerSourceName
from app.repositories.admin_designer_views import AdminDesignerViewRepository
from app.services.catalog.designer_catalog_sync_service import DesignerCatalogSyncService
from app.services.catalog.designer_support import normalize_designer_text


class AdminDesignerViewService:
    def __init__(self, db: Session, admin_user_id: int) -> None:
        self.db = db
        self.admin_user_id = int(admin_user_id)
        self.views = AdminDesignerViewRepository(db)

    def apply_new_flags_to_editor_payload(self, payload: dict) -> dict:
        rows = payload.get("rows") if isinstance(payload, dict) else None
        designers = payload.get("designers") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not isinstance(designers, list):
            return payload

        viewed_source_names = self.views.filter_viewed_source_names(admin_user_id=self.admin_user_id)
        designer_viewed_at = self.views.list_designer_viewed_at(admin_user_id=self.admin_user_id)
        latest_brand_arrival = self._latest_brand_arrival_by_designer_id()

        for row in rows:
            if not isinstance(row, dict):
                continue
            source_brand = normalize_designer_text(row.get("source_brand"))
            row["is_new"] = bool(source_brand) and source_brand not in viewed_source_names

        for designer in designers:
            if not isinstance(designer, dict) or not str(designer.get("id", "") or "").isdigit():
                continue
            designer_id = int(designer["id"])
            viewed_at = designer_viewed_at.get(designer_id)
            latest_arrival = latest_brand_arrival.get(designer_id)
            designer["is_new"] = viewed_at is None or (
                latest_arrival is not None and viewed_at < latest_arrival
            )
        return payload

    def _latest_brand_arrival_by_designer_id(self) -> dict[int, object]:
        rows = (
            self.db.query(DesignerSourceName.designer_id, DesignerSourceName.created_at)
            .filter(DesignerSourceName.designer_id.isnot(None))
            .all()
        )
        latest: dict[int, object] = {}
        for designer_id, created_at in rows:
            if created_at is None:
                continue
            key = int(designer_id)
            if key not in latest or created_at > latest[key]:
                latest[key] = created_at
        return latest

    def mark_brand_viewed(self, source_brand: str) -> None:
        normalized_brand = normalize_designer_text(source_brand)
        if not normalized_brand:
            raise NotFoundError("Бренд-источник не найден")
        counts = DesignerCatalogSyncService(self.db).source_brand_product_counts()
        if normalized_brand not in counts:
            raise NotFoundError("Бренд-источник не найден")
        self.views.mark_brand_viewed(admin_user_id=self.admin_user_id, source_name=normalized_brand)
        self.db.commit()

    def mark_designer_viewed(self, designer_id: int) -> None:
        designer_exists = (
            self.db.query(Designer.id).filter(Designer.id == int(designer_id)).one_or_none()
        )
        if designer_exists is None:
            raise NotFoundError("Дизайнер не найден")
        self.views.mark_designer_viewed(admin_user_id=self.admin_user_id, designer_id=int(designer_id))
        self.db.commit()

    def mark_all_brands_viewed(self) -> int:
        marked = self.views.mark_all_brands_viewed(admin_user_id=self.admin_user_id)
        self.db.commit()
        return marked

    def mark_all_designers_viewed(self) -> int:
        marked = self.views.mark_all_designers_viewed(admin_user_id=self.admin_user_id)
        self.db.commit()
        return marked
