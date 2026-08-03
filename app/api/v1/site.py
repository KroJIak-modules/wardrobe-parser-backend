from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Query, Request, Response
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
def get_site_designers(db: Session = Depends(get_db)) -> SiteDesignersResponse:
    return SiteQueryService(db).designers()


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
    site_access_token: str | None = Cookie(default=None, alias=SITE_ACCESS_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> FileResponse:
    SiteAccessService(db).require_media_access(asset_id, site_access_token)
    service = SiteQueryService(db)
    asset = service.site_media_asset(asset_id)
    file_path = MediaAssetService(db).resolve_file_path(asset)
    return FileResponse(file_path, media_type=asset.mime_type)
