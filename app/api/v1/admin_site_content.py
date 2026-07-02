from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.admin_site_content import (
    AdminSiteAboutResponse,
    AdminSiteAboutUpdateRequest,
    AdminSiteContentMediaUploadResponse,
    AdminSiteNotificationCreateRequest,
    AdminSiteNotificationsResponse,
    AdminSiteQuestionsResponse,
    AdminSiteQuestionsUpdateRequest,
)
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.site_content_service import SiteContentService


router = APIRouter(prefix="/admin/site-content", tags=["admin-site-content"])


@router.get(
    "/about",
    response_model=AdminSiteAboutResponse,
    dependencies=[Depends(require_permission("showcase.read"))],
)
def get_admin_site_about(db: Session = Depends(get_db)) -> AdminSiteAboutResponse:
    return SiteContentService(db).get_admin_about()


@router.put(
    "/about",
    response_model=AdminSiteAboutResponse,
    dependencies=[Depends(require_permission("showcase.edit"))],
)
def put_admin_site_about(payload: AdminSiteAboutUpdateRequest, db: Session = Depends(get_db)) -> AdminSiteAboutResponse:
    result = SiteContentService(db).update_about(payload)
    db.commit()
    return result


@router.get(
    "/questions",
    response_model=AdminSiteQuestionsResponse,
    dependencies=[Depends(require_permission("showcase.read"))],
)
def get_admin_site_questions(db: Session = Depends(get_db)) -> AdminSiteQuestionsResponse:
    return SiteContentService(db).get_admin_questions()


@router.put(
    "/questions",
    response_model=AdminSiteQuestionsResponse,
    dependencies=[Depends(require_permission("showcase.edit"))],
)
def put_admin_site_questions(
    payload: AdminSiteQuestionsUpdateRequest,
    db: Session = Depends(get_db),
) -> AdminSiteQuestionsResponse:
    result = SiteContentService(db).update_questions(payload)
    db.commit()
    return result


@router.get(
    "/notification",
    response_model=AdminSiteNotificationsResponse,
    dependencies=[Depends(require_permission("showcase.read"))],
)
def get_admin_site_notification(db: Session = Depends(get_db)) -> AdminSiteNotificationsResponse:
    return SiteContentService(db).get_admin_notifications()


@router.post(
    "/notification",
    response_model=AdminSiteNotificationsResponse,
    dependencies=[Depends(require_permission("showcase.edit"))],
)
def create_admin_site_notification(
    payload: AdminSiteNotificationCreateRequest,
    db: Session = Depends(get_db),
) -> AdminSiteNotificationsResponse:
    return SiteContentService(db).create_notification(payload)


@router.post(
    "/notification/{notification_id}/reset",
    response_model=AdminSiteNotificationsResponse,
    dependencies=[Depends(require_permission("showcase.edit"))],
)
def reset_admin_site_notification(notification_id: int, db: Session = Depends(get_db)) -> AdminSiteNotificationsResponse:
    return SiteContentService(db).reset_notification_seen_state(notification_id)


@router.delete(
    "/notification/{notification_id}",
    response_model=AdminSiteNotificationsResponse,
    dependencies=[Depends(require_permission("showcase.edit"))],
)
def delete_admin_site_notification(notification_id: int, db: Session = Depends(get_db)) -> AdminSiteNotificationsResponse:
    return SiteContentService(db).delete_notification(notification_id)


@router.post(
    "/media/upload",
    response_model=AdminSiteContentMediaUploadResponse,
    dependencies=[Depends(require_permission("showcase.edit"))],
)
def upload_admin_site_content_media(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> AdminSiteContentMediaUploadResponse:
    result = SiteContentService(db).upload_media(file)
    db.commit()
    return result
