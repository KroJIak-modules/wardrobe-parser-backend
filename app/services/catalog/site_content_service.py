from __future__ import annotations

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.models import (
    ImageAsset,
    SiteAboutPhoto,
    SiteAboutSetting,
    SiteNotificationSetting,
    SiteQuestionItem,
)
from app.schemas.admin_site_content import (
    AdminSiteAboutResponse,
    AdminSiteAboutUpdateRequest,
    AdminSiteContentMediaUploadResponse,
    AdminSiteNotificationCreateRequest,
    AdminSiteNotificationResponse,
    AdminSiteNotificationsResponse,
    AdminSiteQuestionItemResponse,
    AdminSiteQuestionsResponse,
    AdminSiteQuestionsUpdateRequest,
)
from app.schemas.site import (
    SiteAboutResponse,
    SiteHomeNotificationResponse,
    SiteMediaAssetResponse,
    SiteQuestionResponse,
    SiteQuestionsResponse,
)
from app.services.catalog.media_asset_service import MediaAssetService


class SiteContentService:
    ASSET_SCOPE = "site-content"
    NOTIFICATION_ID = "telegram-updates"
    NOTIFICATION_DELAY_MS = 2600

    def __init__(self, db: Session) -> None:
        self.db = db
        self.media_assets = MediaAssetService(db)

    @staticmethod
    def is_site_content_asset(asset: ImageAsset | None) -> bool:
        return bool(asset is not None and MediaAssetService.asset_scope(asset) == SiteContentService.ASSET_SCOPE)

    def _asset_or_error(self, asset_id: int) -> ImageAsset:
        asset = self.db.query(ImageAsset).filter(ImageAsset.id == int(asset_id)).one_or_none()
        if not self.is_site_content_asset(asset):
            raise ValidationError("Медиафайл контента не найден")
        return asset

    def ensure_about(self) -> SiteAboutSetting:
        entity = self.db.query(SiteAboutSetting).order_by(SiteAboutSetting.id.asc()).first()
        if entity is None:
            entity = SiteAboutSetting(id=1)
            self.db.add(entity)
            self.db.flush()
        return entity

    @staticmethod
    def asset_payload(asset: ImageAsset) -> SiteMediaAssetResponse:
        return SiteMediaAssetResponse(
            id=int(asset.id),
            url=f"/api/v1/site/media/{int(asset.id)}/file",
            media_kind=MediaAssetService.media_kind_for_asset(asset),  # type: ignore[arg-type]
            mime_type=str(asset.mime_type or "").strip() or "application/octet-stream",
            byte_size=int(asset.byte_size or 0),
            width_px=(int(asset.width_px) if asset.width_px is not None else None),
            height_px=(int(asset.height_px) if asset.height_px is not None else None),
        )

    @staticmethod
    def admin_asset_payload(asset: ImageAsset) -> SiteMediaAssetResponse:
        return SiteMediaAssetResponse(
            id=int(asset.id),
            url=f"/api/v1/admin/site-content/media/{int(asset.id)}/file",
            media_kind=MediaAssetService.media_kind_for_asset(asset),  # type: ignore[arg-type]
            mime_type=str(asset.mime_type or "").strip() or "application/octet-stream",
            byte_size=int(asset.byte_size or 0),
            width_px=(int(asset.width_px) if asset.width_px is not None else None),
            height_px=(int(asset.height_px) if asset.height_px is not None else None),
        )

    def admin_media_asset(self, asset_id: int) -> ImageAsset:
        asset = self.db.query(ImageAsset).filter(ImageAsset.id == int(asset_id)).one_or_none()
        if not self.is_site_content_asset(asset):
            raise NotFoundError("Медиафайл контента не найден")
        return asset

    def get_admin_about(self) -> AdminSiteAboutResponse:
        about = self.ensure_about()
        rows = (
            self.db.query(SiteAboutPhoto)
            .join(ImageAsset, ImageAsset.id == SiteAboutPhoto.image_asset_id)
            .order_by(SiteAboutPhoto.position.asc(), SiteAboutPhoto.id.asc())
            .all()
        )
        return AdminSiteAboutResponse(
            text=str(about.body_text or ""),
            photos=[
                self.admin_asset_payload(row.image_asset)
                for row in rows
                if self.is_site_content_asset(getattr(row, "image_asset", None))
            ],
        )

    def update_about(self, payload: AdminSiteAboutUpdateRequest) -> AdminSiteAboutResponse:
        about = self.ensure_about()
        about.body_text = str(payload.text or "")
        rows = self.db.query(SiteAboutPhoto).order_by(SiteAboutPhoto.position.asc(), SiteAboutPhoto.id.asc()).all()
        for row in rows:
            self.db.delete(row)
        self.db.flush()
        normalized_asset_ids: list[int] = []
        seen: set[int] = set()
        for raw_asset_id in payload.photo_asset_ids:
            asset_id = int(raw_asset_id)
            if asset_id <= 0 or asset_id in seen:
                continue
            self._asset_or_error(asset_id)
            seen.add(asset_id)
            normalized_asset_ids.append(asset_id)
        for position, asset_id in enumerate(normalized_asset_ids, start=1):
            self.db.add(SiteAboutPhoto(image_asset_id=asset_id, position=position))
        self.db.flush()
        return self.get_admin_about()

    def get_public_about(self) -> SiteAboutResponse:
        about = self.ensure_about()
        rows = (
            self.db.query(SiteAboutPhoto)
            .join(ImageAsset, ImageAsset.id == SiteAboutPhoto.image_asset_id)
            .order_by(SiteAboutPhoto.position.asc(), SiteAboutPhoto.id.asc())
            .all()
        )
        return SiteAboutResponse(
            text=str(about.body_text or ""),
            photos=[
                self.asset_payload(row.image_asset)
                for row in rows
                if self.is_site_content_asset(getattr(row, "image_asset", None))
            ],
        )

    def upload_media(self, upload: UploadFile) -> AdminSiteContentMediaUploadResponse:
        asset = self.media_assets.save_upload(scope=self.ASSET_SCOPE, upload=upload)
        if MediaAssetService.media_kind_for_asset(asset) != "image":
            raise ValidationError("Для контента можно загружать только изображения.")
        return AdminSiteContentMediaUploadResponse(ok=True, asset=self.admin_asset_payload(asset))

    def _notification_response(self, entity: SiteNotificationSetting) -> AdminSiteNotificationResponse:
        image_asset = getattr(entity, "image_asset", None)
        return AdminSiteNotificationResponse(
            id=int(entity.id),
            version=int(entity.version),
            title=str(entity.title or ""),
            description=str(entity.description or ""),
            button_text=str(entity.button_text or ""),
            button_url=str(entity.button_url or ""),
            image=self.admin_asset_payload(image_asset) if self.is_site_content_asset(image_asset) else None,
            created_at=entity.created_at.isoformat() if entity.created_at is not None else "",
            updated_at=entity.updated_at.isoformat() if entity.updated_at is not None else "",
        )

    def get_admin_notifications(self) -> AdminSiteNotificationsResponse:
        items = (
            self.db.query(SiteNotificationSetting)
            .filter(SiteNotificationSetting.deleted_at.is_(None))
            .order_by(SiteNotificationSetting.created_at.desc(), SiteNotificationSetting.id.desc())
            .all()
        )
        return AdminSiteNotificationsResponse(items=[self._notification_response(item) for item in items])

    @staticmethod
    def _validate_notification_text(payload: AdminSiteNotificationCreateRequest) -> tuple[str, str, str, str]:
        title = str(payload.title or "").strip()
        description = str(payload.description or "").strip()
        button_text = str(payload.button_text or "").strip()
        button_url = str(payload.button_url or "").strip()
        if not title:
            raise ValidationError("Заголовок уведомления обязателен")
        if not description:
            raise ValidationError("Описание уведомления обязательно")
        if not button_text:
            raise ValidationError("Текст кнопки уведомления обязателен")
        if not button_url:
            raise ValidationError("Ссылка кнопки уведомления обязательна")
        if not (button_url.startswith("/") or button_url.startswith("http://") or button_url.startswith("https://")):
            raise ValidationError("Ссылка кнопки должна быть относительной или начинаться с http:// или https://")
        return title, description, button_text, button_url

    def create_notification(self, payload: AdminSiteNotificationCreateRequest) -> AdminSiteNotificationsResponse:
        try:
            title, description, button_text, button_url = self._validate_notification_text(payload)
            image_asset_id = int(payload.image_asset_id) if payload.image_asset_id is not None else None
            if image_asset_id is None:
                raise ValidationError("Фото уведомления обязательно")
            self._asset_or_error(image_asset_id)
            self.db.add(
                SiteNotificationSetting(
                    title=title,
                    description=description,
                    button_text=button_text,
                    button_url=button_url,
                    image_asset_id=image_asset_id,
                    version=1,
                )
            )
            self.db.flush()
            response = self.get_admin_notifications()
            self.db.commit()
            return response
        except Exception:
            self.db.rollback()
            raise

    def _notification_or_error(self, notification_id: int) -> SiteNotificationSetting:
        entity = (
            self.db.query(SiteNotificationSetting)
            .filter(
                SiteNotificationSetting.id == int(notification_id),
                SiteNotificationSetting.deleted_at.is_(None),
            )
            .one_or_none()
        )
        if entity is None:
            raise NotFoundError("Уведомление не найдено")
        return entity

    def reset_notification_seen_state(self, notification_id: int) -> AdminSiteNotificationsResponse:
        try:
            entity = self._notification_or_error(notification_id)
            entity.version = int(entity.version or 1) + 1
            self.db.flush()
            response = self.get_admin_notifications()
            self.db.commit()
            return response
        except Exception:
            self.db.rollback()
            raise

    def delete_notification(self, notification_id: int) -> AdminSiteNotificationsResponse:
        try:
            entity = self._notification_or_error(notification_id)
            self.db.delete(entity)
            self.db.flush()
            response = self.get_admin_notifications()
            self.db.commit()
            return response
        except Exception:
            self.db.rollback()
            raise

    def get_public_notification(self) -> SiteHomeNotificationResponse:
        entity = (
            self.db.query(SiteNotificationSetting)
            .join(ImageAsset, ImageAsset.id == SiteNotificationSetting.image_asset_id)
            .filter(
                SiteNotificationSetting.deleted_at.is_(None),
                SiteNotificationSetting.title != "",
                SiteNotificationSetting.description != "",
                SiteNotificationSetting.button_text != "",
                SiteNotificationSetting.button_url != "",
                ImageAsset.scope == self.ASSET_SCOPE,
            )
            .order_by(SiteNotificationSetting.created_at.desc(), SiteNotificationSetting.id.desc())
            .first()
        )
        if entity is None:
            return SiteHomeNotificationResponse(
                id=self.NOTIFICATION_ID,
                version="v0",
                enabled=False,
                delay_ms=self.NOTIFICATION_DELAY_MS,
                title="",
                description="",
                image_src="",
                cta_label="",
                cta_href="",
            )
        image_asset = getattr(entity, "image_asset", None)
        image_src = self.asset_payload(image_asset).url if self.is_site_content_asset(image_asset) else ""
        title = str(entity.title or "").strip()
        description = str(entity.description or "").strip()
        cta_label = str(entity.button_text or "").strip()
        cta_href = str(entity.button_url or "").strip()
        return SiteHomeNotificationResponse(
            id=f"{self.NOTIFICATION_ID}:{int(entity.id)}",
            version=f"v{int(entity.version or 1)}",
            enabled=bool(title and description and cta_label and cta_href and image_src),
            delay_ms=self.NOTIFICATION_DELAY_MS,
            title=title,
            description=description,
            image_src=image_src,
            cta_label=cta_label,
            cta_href=cta_href,
        )

    def _question_response(self, item: SiteQuestionItem) -> AdminSiteQuestionItemResponse:
        return AdminSiteQuestionItemResponse(
            id=int(item.id),
            question=str(item.question or ""),
            answer=str(item.answer or ""),
            is_enabled=bool(item.is_enabled),
            is_expanded_by_default=bool(item.is_expanded_by_default),
            position=int(item.position),
        )

    def get_admin_questions(self) -> AdminSiteQuestionsResponse:
        items = (
            self.db.query(SiteQuestionItem)
            .order_by(SiteQuestionItem.position.asc(), SiteQuestionItem.id.asc())
            .all()
        )
        return AdminSiteQuestionsResponse(items=[self._question_response(item) for item in items])

    def update_questions(self, payload: AdminSiteQuestionsUpdateRequest) -> AdminSiteQuestionsResponse:
        existing_by_id = {
            int(item.id): item
            for item in self.db.query(SiteQuestionItem).order_by(SiteQuestionItem.position.asc(), SiteQuestionItem.id.asc()).all()
        }
        seen_ids: set[int] = set()
        ordered_items: list[SiteQuestionItem] = []
        for position, raw_item in enumerate(payload.items, start=1):
            entity: SiteQuestionItem
            raw_id = int(raw_item.id) if raw_item.id is not None else None
            if raw_id is not None:
                entity = existing_by_id.get(raw_id)  # type: ignore[assignment]
                if entity is None:
                    raise NotFoundError("Вопрос не найден")
                seen_ids.add(raw_id)
            else:
                entity = SiteQuestionItem()
                self.db.add(entity)
            entity.question = str(raw_item.question or "")
            entity.answer = str(raw_item.answer or "")
            entity.is_enabled = bool(raw_item.is_enabled)
            entity.is_expanded_by_default = bool(raw_item.is_expanded_by_default)
            entity.position = position
            ordered_items.append(entity)
        for item_id, entity in existing_by_id.items():
            if item_id not in seen_ids:
                self.db.delete(entity)
        self.db.flush()
        return AdminSiteQuestionsResponse(items=[self._question_response(item) for item in ordered_items])

    def get_public_questions(self) -> SiteQuestionsResponse:
        items = (
            self.db.query(SiteQuestionItem)
            .filter(SiteQuestionItem.is_enabled.is_(True))
            .order_by(SiteQuestionItem.position.asc(), SiteQuestionItem.id.asc())
            .all()
        )
        return SiteQuestionsResponse(
            items=[
                SiteQuestionResponse(
                    id=int(item.id),
                    question=str(item.question or ""),
                    answer=str(item.answer or ""),
                    is_expanded_by_default=bool(item.is_expanded_by_default),
                )
                for item in items
            ]
        )
