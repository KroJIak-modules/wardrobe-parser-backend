from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ShowcaseCarouselImage, ShowcaseSetting


class ShowcaseService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def ensure_settings(self) -> ShowcaseSetting:
        entity = self.db.query(ShowcaseSetting).order_by(ShowcaseSetting.id.asc()).first()
        if entity is None:
            entity = ShowcaseSetting(id=1)
            self.db.add(entity)
            self.db.flush()
        return entity

    def state(self) -> dict:
        settings = self.ensure_settings()
        carousel_items = (
            self.db.query(ShowcaseCarouselImage)
            .order_by(ShowcaseCarouselImage.position.asc(), ShowcaseCarouselImage.id.asc())
            .all()
        )
        return {
            "hero_image_asset_id": int(settings.hero_image_asset_id) if settings.hero_image_asset_id else None,
            "carousel_image_asset_ids": [int(item.image_asset_id) for item in carousel_items],
        }
