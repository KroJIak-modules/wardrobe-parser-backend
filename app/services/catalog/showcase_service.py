from __future__ import annotations

from typing import Literal

from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models import ImageAsset
from app.models import ShowcaseCarouselImage, ShowcaseSetting
from app.schemas.showcase_media import (
    ShowcaseMediaAssetResponse,
    ShowcaseStateResponse,
    ShowcaseStateUpdateRequest,
    ShowcaseViewport,
    ShowcaseViewportStateResponse,
)
from app.services.catalog.media_asset_service import MediaAssetService


ShowcaseViewportKey = Literal["desktop", "mobile"]


class ShowcaseService:
    CAROUSEL_LIMIT = 20
    VIEWPORTS: tuple[ShowcaseViewportKey, ShowcaseViewportKey] = ("desktop", "mobile")

    def __init__(self, db: Session) -> None:
        self.db = db
        self.media_assets = MediaAssetService(db)

    @staticmethod
    def is_showcase_asset(asset: ImageAsset | None) -> bool:
        return bool(asset is not None and MediaAssetService.asset_scope(asset) == "showcase")

    def ensure_settings(self) -> ShowcaseSetting:
        entity = self.db.query(ShowcaseSetting).order_by(ShowcaseSetting.id.asc()).first()
        if entity is None:
            entity = ShowcaseSetting(id=1)
            self.db.add(entity)
            self.db.flush()
        return entity

    @staticmethod
    def _hero_attr_name(viewport: ShowcaseViewport) -> str:
        return f"{viewport}_hero_image_asset_id"

    def _asset_or_error(self, asset_id: int) -> ImageAsset:
        asset = self.db.query(ImageAsset).filter(ImageAsset.id == int(asset_id)).one_or_none()
        if not self.is_showcase_asset(asset):
            raise ValidationError("Медиафайл витрины не найден")
        self.media_assets.validate_existing_showcase_asset(asset)
        return asset

    @staticmethod
    def asset_payload(asset: ImageAsset) -> ShowcaseMediaAssetResponse:
        return ShowcaseMediaAssetResponse(
            id=int(asset.id),
            mime_type=str(asset.mime_type or "").strip() or "application/octet-stream",
            media_kind=MediaAssetService.media_kind_for_asset(asset),  # type: ignore[arg-type]
            byte_size=int(asset.byte_size or 0),
            width_px=(int(asset.width_px) if asset.width_px is not None else None),
            height_px=(int(asset.height_px) if asset.height_px is not None else None),
        )

    def _viewport_state(self, *, viewport: ShowcaseViewport, settings: ShowcaseSetting) -> ShowcaseViewportStateResponse:
        hero_asset_id = getattr(settings, self._hero_attr_name(viewport))
        hero_asset = None
        if hero_asset_id:
            asset = self.db.query(ImageAsset).filter(ImageAsset.id == int(hero_asset_id)).one_or_none()
            if self.is_showcase_asset(asset):
                hero_asset = self.asset_payload(asset)
        carousel_items = (
            self.db.query(ShowcaseCarouselImage)
            .join(ImageAsset, ImageAsset.id == ShowcaseCarouselImage.image_asset_id)
            .filter(ShowcaseCarouselImage.viewport == viewport)
            .order_by(ShowcaseCarouselImage.position.asc(), ShowcaseCarouselImage.id.asc())
            .all()
        )
        return ShowcaseViewportStateResponse(
            hero_asset=hero_asset,
            carousel_assets=[
                self.asset_payload(item.image_asset)
                for item in carousel_items
                if self.is_showcase_asset(getattr(item, "image_asset", None))
            ],
        )

    def state(self) -> ShowcaseStateResponse:
        settings = self.ensure_settings()
        return ShowcaseStateResponse(
            desktop=self._viewport_state(viewport="desktop", settings=settings),
            mobile=self._viewport_state(viewport="mobile", settings=settings),
            carousel_limit=self.CAROUSEL_LIMIT,
        )

    @staticmethod
    def _normalize_asset_ids(items: list[int]) -> list[int]:
        normalized: list[int] = []
        seen: set[int] = set()
        for raw in items:
            asset_id = int(raw)
            if asset_id <= 0 or asset_id in seen:
                continue
            seen.add(asset_id)
            normalized.append(asset_id)
        return normalized

    def replace_state(self, payload: ShowcaseStateUpdateRequest) -> ShowcaseStateResponse:
        settings = self.ensure_settings()
        current_rows = (
            self.db.query(ShowcaseCarouselImage)
            .order_by(ShowcaseCarouselImage.viewport.asc(), ShowcaseCarouselImage.position.asc(), ShowcaseCarouselImage.id.asc())
            .all()
        )
        rows_by_viewport = {
            viewport: [row for row in current_rows if str(row.viewport or "").strip() == viewport]
            for viewport in self.VIEWPORTS
        }
        for viewport in self.VIEWPORTS:
            viewport_payload = getattr(payload, viewport)
            hero_asset_id = int(viewport_payload.hero_asset_id) if viewport_payload.hero_asset_id is not None else None
            if hero_asset_id is not None:
                self._asset_or_error(hero_asset_id)
            setattr(settings, self._hero_attr_name(viewport), hero_asset_id)

            carousel_asset_ids = self._normalize_asset_ids(viewport_payload.carousel_asset_ids)
            if len(carousel_asset_ids) > self.CAROUSEL_LIMIT:
                raise ValidationError(f"В карусели можно хранить максимум {self.CAROUSEL_LIMIT} медиафайлов.")
            for asset_id in carousel_asset_ids:
                self._asset_or_error(asset_id)

            for row in rows_by_viewport[viewport]:
                self.db.delete(row)
            self.db.flush()
            for position, asset_id in enumerate(carousel_asset_ids, start=1):
                self.db.add(
                    ShowcaseCarouselImage(
                        image_asset_id=asset_id,
                        viewport=viewport,
                        position=position,
                    )
                )
            self.db.flush()
        return self.state()
