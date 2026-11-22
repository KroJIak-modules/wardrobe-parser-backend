from __future__ import annotations

from datetime import timezone
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, Header, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.services.catalog.cart_pricing_service import CartPricingService
from app.services.catalog.media_asset_service import MediaAssetService
from app.services.catalog.site_access_service import SITE_ACCESS_COOKIE_NAME, SiteAccessService, require_site_access
from app.services.catalog.site_query_service import SiteQueryService
from app.schemas.site import (
    SiteAboutResponse,
    SiteAccessStatusResponse,
    SiteAccessUnlockRequest,
    SiteAccessUnlockResponse,
    SiteCarouselResponse,
    SiteCartQuoteRequest,
    SiteCartQuoteResponse,
    SiteCatalogExperienceResponse,
    SiteCatalogProductsResponse,
    SiteDesignersResponse,
    SiteHomeNotificationResponse,
    SiteHeroResponse,
    SiteNavigationResponse,
    SiteProductResponse,
    SiteQuestionsResponse,
)


router = APIRouter(prefix="/site", tags=["site"])


@router.get("/home/hero", response_model=SiteHeroResponse)
def get_site_home_hero(
    viewport: str = Query(default="desktop"),
    db: Session = Depends(get_db),
) -> SiteHeroResponse:
    normalized_viewport = "mobile" if str(viewport or "").strip().lower() == "mobile" else "desktop"
    return SiteQueryService(db).home_hero(normalized_viewport)


@router.get("/access/status", response_model=SiteAccessStatusResponse)
def get_site_access_status(
    site_access_token: str | None = Cookie(default=None, alias=SITE_ACCESS_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> SiteAccessStatusResponse:
    return SiteAccessService(db).public_status(site_access_token)


@router.post("/access/unlock", response_model=SiteAccessUnlockResponse)
def unlock_site_access(
    payload: SiteAccessUnlockRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> SiteAccessUnlockResponse:
    result = SiteAccessService(db).unlock(payload.password)
    if result.token:
        response.set_cookie(
            key=SITE_ACCESS_COOKIE_NAME,
            value=result.token,
            httponly=True,
            secure=settings.admin_auth_cookie_secure,
            samesite="lax",
            max_age=result.max_age,
            path="/",
        )
    return result.response


@router.get("/home/carousel", response_model=SiteCarouselResponse, dependencies=[Depends(require_site_access)])
def get_site_home_carousel(
    viewport: str = Query(default="desktop"),
    db: Session = Depends(get_db),
) -> SiteCarouselResponse:
    normalized_viewport = "mobile" if str(viewport or "").strip().lower() == "mobile" else "desktop"
    return SiteQueryService(db).home_carousel(normalized_viewport)


@router.get("/home/notification", response_model=SiteHomeNotificationResponse, dependencies=[Depends(require_site_access)])
def get_site_home_notification(db: Session = Depends(get_db)) -> SiteHomeNotificationResponse:
    return SiteQueryService(db).home_notification()


@router.get("/navigation", response_model=SiteNavigationResponse, dependencies=[Depends(require_site_access)])
def get_site_navigation(db: Session = Depends(get_db)) -> SiteNavigationResponse:
    return SiteQueryService(db).navigation()


@router.get("/catalog/experience", response_model=SiteCatalogExperienceResponse, dependencies=[Depends(require_site_access)])
def get_site_catalog_experience(
    request: Request,
    view_key: str = Query(default="default"),
    db: Session = Depends(get_db),
) -> SiteCatalogExperienceResponse:
    normalized_view_key = str(view_key or "").strip().lower()
    if normalized_view_key not in {"default", "designers", "sale"}:
        normalized_view_key = "default"
    search_params = {
        key: request.query_params.getlist(key)
        for key in request.query_params.keys()
        if key != "view_key"
    }
    return SiteQueryService(db).catalog_experience(view_key=normalized_view_key, search_params=search_params)


@router.get("/catalog/products", response_model=SiteCatalogProductsResponse, dependencies=[Depends(require_site_access)])
def get_site_catalog_products(
    q: str = Query(default=""),
    designer: list[str] = Query(default=[]),
    gender: list[str] = Query(default=[]),
    section: list[str] = Query(default=[]),
    collection: str | None = Query(default=None),
    availability: str | None = Query(default=None),
    status: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    discounted_only: bool = Query(default=False),
    limit: int = Query(default=48, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> SiteCatalogProductsResponse:
    normalized_availability = str(availability or "").strip().lower() or None
    if normalized_availability not in {None, "in_stock", "in-stock", "preorder", "by_order"}:
        normalized_availability = None
    normalized_status = str(status or "").strip().lower() or None
    if normalized_status not in {None, "orderable", "sold_out"}:
        normalized_status = None
    return SiteQueryService(db).catalog_products(
        limit=limit,
        offset=offset,
        query=q,
        designer_slugs=designer,
        gender_values=gender,
        filter_slugs=section,
        custom_catalog_slug=collection,
        availability_mode=(
            "in_stock"
            if normalized_availability in {"in_stock", "in-stock"}
            else "by_order"
            if normalized_availability in {"preorder", "by_order"}
            else None
        ),
        orderability_status=normalized_status,
        discounted_only=discounted_only,
        sort=sort,
    )


@router.post("/cart/quote", response_model=SiteCartQuoteResponse, dependencies=[Depends(require_site_access)])
def quote_site_cart(
    payload: SiteCartQuoteRequest,
    db: Session = Depends(get_db),
) -> SiteCartQuoteResponse:
    return CartPricingService(db).quote(payload.items)


@router.get("/designers", response_model=SiteDesignersResponse, dependencies=[Depends(require_site_access)])
def get_site_designers(
    designer: str = Query(default="", description="Выбранные дизайнеры через запятую"),
    db: Session = Depends(get_db),
) -> SiteDesignersResponse:
    # Выбранных дизайнеров показываем в списке, даже если у них сейчас нет
    # публичных товаров: иначе выбор нельзя ни увидеть, ни снять.
    selected_slugs = [item.strip() for item in str(designer or "").split(",") if item.strip()]
    return SiteQueryService(db).designers(selected_slugs=selected_slugs)


@router.get("/products/{product_path}", response_model=SiteProductResponse, dependencies=[Depends(require_site_access)])
def get_site_product(product_path: str, db: Session = Depends(get_db)) -> SiteProductResponse:
    return SiteQueryService(db).product(product_path)


@router.get("/about", response_model=SiteAboutResponse, dependencies=[Depends(require_site_access)])
def get_site_about(db: Session = Depends(get_db)) -> SiteAboutResponse:
    return SiteQueryService(db).about()


@router.get("/questions", response_model=SiteQuestionsResponse, dependencies=[Depends(require_site_access)])
def get_site_questions(db: Session = Depends(get_db)) -> SiteQuestionsResponse:
    return SiteQueryService(db).questions()


@router.get("/media/{asset_id}/file")
def get_site_media_file(
    asset_id: int,
    request: Request,
    w: int | None = Query(default=None, ge=64, le=4000),
    q: int = Query(default=82, ge=40, le=100),
    fm: str | None = Query(default=None),
    site_access_token: str | None = Cookie(default=None, alias=SITE_ACCESS_COOKIE_NAME),
    x_accel_enabled: str | None = Header(default=None, alias="X-Accel-Enabled"),
    db: Session = Depends(get_db),
) -> Response:
    SiteAccessService(db).require_media_access(asset_id, site_access_token)
    service = SiteQueryService(db)
    asset = service.site_media_asset(asset_id)
    media_service = MediaAssetService(db)
    if w is not None or fm is not None:
        variant = media_service.render_variant_file(asset, width=w, quality=q, fmt=fm)
        if variant is not None:
            variant_path, variant_mime = variant
            return _media_file_response(
                path=variant_path,
                mime_type=variant_mime,
                via_nginx=str(x_accel_enabled or "").strip() == "1",
                request=request,
            )

    file_path = media_service.resolve_file_path(asset)
    return _media_file_response(
        path=file_path,
        mime_type=asset.mime_type,
        via_nginx=str(x_accel_enabled or "").strip() == "1",
        request=request,
    )


def _media_file_response(
    *,
    path: Path,
    mime_type: str,
    via_nginx: bool,
    request: Request | None = None,
) -> Response:
    """Отдать файл витрины.

    Когда запрос прошёл через nginx, файл отдаёт сам nginx: приложение только
    проверяет доступ и отвечает внутренним редиректом. Так изображение не
    проходит через Python и уходит клиенту напрямую с диска.

    Кэш короткий: фото в админке меняют, и старая картинка не должна жить в
    браузере неделями. Вместо долгого срока отдаём ETag, поэтому браузер
    после истечения кэша спрашивает условным запросом и получает 304 без тела.
    """
    cache_control = {"Cache-Control": "private, max-age=300, must-revalidate"}
    if via_nginx:
        internal_path = MediaAssetService.internal_media_path(path)
        if internal_path is not None:
            # Кэширование ответа задаёт nginx в своей внутренней локации,
            # поэтому здесь его не дублируем.
            return Response(
                content=b"",
                media_type=mime_type,
                headers={"X-Accel-Redirect": internal_path},
            )

    stat = path.stat()
    etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
    headers = {
        **cache_control,
        "ETag": etag,
        "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
    }
    if request is not None and _media_not_modified(request, etag=etag, mtime=stat.st_mtime):
        return Response(status_code=304, headers=headers)

    return FileResponse(path, media_type=mime_type, headers=headers)


def _media_not_modified(request: Request, *, etag: str, mtime: float) -> bool:
    """Проверить условный запрос клиента: файл не менялся — отвечаем 304."""
    if_none_match = str(request.headers.get("if-none-match") or "").strip()
    if if_none_match:
        candidates = {item.strip() for item in if_none_match.split(",")}
        return etag in candidates or "*" in candidates

    if_modified_since = str(request.headers.get("if-modified-since") or "").strip()
    if not if_modified_since:
        return False

    try:
        since = parsedate_to_datetime(if_modified_since)
    except (TypeError, ValueError):
        return False

    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return int(mtime) <= int(since.timestamp())
