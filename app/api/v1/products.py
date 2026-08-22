from __future__ import annotations

from pathlib import Path
import time
import requests
from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import NotFoundError, ValidationError
from app.models import ImageAsset, Product, ProductListing, ProductListingMember
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.media_asset_service import MediaAssetService
from app.services.catalog.sync_error_humanizer import humanize_sync_error, normalize_sync_error_code
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.product_query_service import ProductQueryService
from app.services.catalog.designer_catalog_sync_service import DesignerCatalogSyncService
from app.services.catalog.product_write_service import ProductWriteService
from app.services.catalog.source_registry_service import SourceRegistryService


router = APIRouter(tags=["products"])

_PROBE_MAX_ATTEMPTS = 3
_PROBE_POLL_ATTEMPTS = 180
_PROBE_POLL_INTERVAL_SEC = 1.0
_PROBE_RETRY_DELAY_SEC = 1.0


def _get_admin_mutation_payload_or_404(db: Session, product_id: int) -> dict:
    payload = ProductQueryService(db).get_admin_mutation_payload(product_id)
    if payload is None:
        raise NotFoundError("Товар не найден")
    return payload


class ManualVariantRequest(BaseModel):
    title: str
    price: float | None = None
    compare_at_price: float | None = None
    currency: str = "RUB"
    available: bool = True
    pricing_mode: str | None = None


class ProductPatchRequest(BaseModel):
    title_override: str | None = None
    brand_override_name: str | None = None
    description_text: str | None = None
    description_html: str | None = None
    description_visibility: bool | None = None
    visibility_status: str | None = None
    availability_mode: str | None = None
    gender: str | None = None
    primary_listing_id: int | None = None
    manual_weight_grams: int | None = None
    images: dict | None = None
    gallery_listing_id: int | None = None
    filter_slugs: list[str] | None = None
    custom_catalog_slugs: list[str] | None = None
    reset_to_default: list[str] = Field(default_factory=list)


class ProductBulkPatchRequest(BaseModel):
    product_ids: list[int] = Field(default_factory=list, min_length=1)
    gender: str | None = None


class ManualProductRequest(BaseModel):
    title: str
    description_text: str | None = None
    description_html: str | None = None
    designer_id: int | None = None
    designer_name: str | None = None
    source_category_name: str | None = None
    gender: str = "unisex"
    availability_mode: str = "in_stock"
    visibility_status: str = "visible"
    orderability_status: str = "orderable"
    variants: list[ManualVariantRequest] = Field(default_factory=list)
    manual_image_asset_ids: list[int] = Field(default_factory=list)
    manual_weight_grams: int | None = None
    filter_slugs: list[str] = Field(default_factory=list)
    custom_catalog_slugs: list[str] = Field(default_factory=list)
    bind_source_url: str | None = None
    bind_source_as_primary: bool = False


class ManualProductPatchRequest(BaseModel):
    title: str | None = None
    description_text: str | None = None
    description_html: str | None = None
    designer_id: int | None = None
    designer_name: str | None = None
    source_category_name: str | None = None
    gender: str | None = None
    availability_mode: str | None = None
    visibility_status: str | None = None
    orderability_status: str | None = None
    variants: list[ManualVariantRequest] | None = None
    manual_image_asset_ids: list[int] | None = None
    gallery_listing_id: int | None = None
    manual_weight_grams: int | None = None
    filter_slugs: list[str] | None = None
    custom_catalog_slugs: list[str] | None = None


class ManualVariantsPatchRequest(BaseModel):
    variants: list[ManualVariantRequest] = Field(default_factory=list)


class ProductUrlRequest(BaseModel):
    url: str


class BindSourceByUrlRequest(BaseModel):
    url: str
    set_as_primary: bool = False


def _service_url(path: str) -> str:
    return f"{settings.service_base_url.rstrip('/')}/api/v1/sync{path}"


def _fetch_probe_job_events(job_id: str) -> list[dict]:
    events_res = requests.get(_service_url(f"/probe/jobs/{job_id}/events"), params={"cursor": 0, "limit": 500}, timeout=(5, 20))
    events_res.raise_for_status()
    events_payload = events_res.json() if isinstance(events_res.json(), dict) else {}
    return [item for item in (events_payload.get("items") if isinstance(events_payload.get("items"), list) else []) if isinstance(item, dict)]


def _cancel_probe_job(job_id: str) -> None:
    try:
        cancel_res = requests.post(_service_url(f"/probe/jobs/{job_id}/cancel"), timeout=(5, 10))
        cancel_res.raise_for_status()
    except requests.RequestException:
        return


def _extract_probe_item(events: list[dict]) -> dict | None:
    for event in events:
        if str(event.get("type") or "").strip() != "product_batch":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        for item in items:
            if isinstance(item, dict) and str(item.get("url") or "").strip():
                return item
    return None


def _looks_like_probe_transport_failure(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(
        marker in lowered
        for marker in (
            "incompleteread",
            "chunkedencodingerror",
            "connection broken",
            "protocolerror",
            "read timeout",
            "timed out",
            "connection reset",
            "remote end closed connection",
        )
    )


def _extract_probe_failure(events: list[dict], status_payload: dict) -> tuple[str | None, str | None]:
    status_error = str(status_payload.get("error") or "").strip()
    report_error = ""
    fetch_skip_reason = ""

    for event in reversed(events):
        if str(event.get("type") or "").strip() == "source_progress":
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if str(payload.get("stage") or "").strip() != "fetch_skip":
                continue
            fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
            fetch_skip_reason = str(fields.get("reason") or payload.get("reason") or "").strip()
            if fetch_skip_reason:
                break

    for event in reversed(events):
        if str(event.get("type") or "").strip() != "source_finished":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        error_code = str(payload.get("error_code") or "").strip() or None
        error_message = str(payload.get("error_message") or "").strip()
        error_block = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        report_errors = error_block.get("report_errors") if isinstance(error_block.get("report_errors"), list) else []
        report_error = next((str(item).strip() for item in reversed(report_errors) if str(item).strip()), "")
        candidate_message = error_message or report_error or fetch_skip_reason or status_error
        if _looks_like_probe_transport_failure(candidate_message):
            return ("temporary_source_failure", candidate_message)
        normalized_code = normalize_sync_error_code(candidate_message, error_code)
        if normalized_code in {"service_unavailable", "service_timeout"}:
            return ("temporary_source_failure", candidate_message)
        if report_error.startswith("product_not_found:") or error_message.startswith("product_not_found:"):
            return ("product_not_found", None)
        if error_message or report_error:
            return (error_code or normalized_code or "probe_failed", candidate_message)

    if _looks_like_probe_transport_failure(fetch_skip_reason or status_error):
        return ("temporary_source_failure", fetch_skip_reason or status_error)
    if status_error.startswith("product_not_found:"):
        return ("product_not_found", None)
    if status_error:
        normalized_code = normalize_sync_error_code(status_error)
        if normalized_code in {"service_unavailable", "service_timeout"}:
            return ("temporary_source_failure", status_error)
        return (normalized_code or "probe_failed", status_error)
    return (None, None)


def _probe_temporary_failure_message(error_message: str | None, error_code: str | None = None) -> str:
    if _looks_like_probe_transport_failure(error_message or ""):
        return "Источник временно оборвал загрузку страницы товара. Попробуй повторить еще раз."
    return humanize_sync_error(error_message, error_code) or "Источник временно не смог отдать данные по ссылке. Попробуй повторить позже."


def _probe_service_product(url: str) -> dict:
    last_failure_kind: str | None = None
    last_failure_message: str | None = None
    last_failure_code: str | None = None

    for attempt_index in range(_PROBE_MAX_ATTEMPTS):
        is_last_attempt = attempt_index == _PROBE_MAX_ATTEMPTS - 1
        create_res = requests.post(_service_url("/probe/jobs"), json={"product_url": url, "dry_run": True}, timeout=(5, 30))
        try:
            create_res.raise_for_status()
        except requests.HTTPError as exc:
            if create_res.status_code == 409:
                raise ValidationError("Предыдущая проверка ссылки еще не завершилась. Подожди немного и попробуй снова.") from exc
            raise
        create_payload = create_res.json() if isinstance(create_res.json(), dict) else {}
        job_id = str(create_payload.get("job_id") or "").strip()
        if not job_id:
            raise ValidationError("service probe did not return job_id")

        status_payload: dict = {}
        status_value = ""
        for _ in range(_PROBE_POLL_ATTEMPTS):
            status_res = requests.get(_service_url(f"/probe/jobs/{job_id}"), timeout=(5, 20))
            status_res.raise_for_status()
            status_payload = status_res.json() if isinstance(status_res.json(), dict) else {}
            status_value = str(status_payload.get("status") or "").strip().lower()
            if status_value in {"completed", "failed", "canceled"}:
                break
            time.sleep(_PROBE_POLL_INTERVAL_SEC)

        if status_value not in {"completed", "failed", "canceled"}:
            _cancel_probe_job(job_id)
            raise ValidationError("Источник отвечает слишком долго. Попробуй повторить еще раз позже.")

        events = _fetch_probe_job_events(job_id)
        item = _extract_probe_item(events)
        if item is not None:
            return item

        failure_kind, failure_message = _extract_probe_failure(events, status_payload)
        last_failure_kind = failure_kind
        last_failure_message = failure_message
        last_failure_code = failure_kind
        if failure_kind == "temporary_source_failure" and not is_last_attempt:
            time.sleep(_PROBE_RETRY_DELAY_SEC)
            continue
        break
    if last_failure_kind == "temporary_source_failure":
        raise ValidationError(_probe_temporary_failure_message(last_failure_message, last_failure_code))
    raise NotFoundError("Товар по URL не найден")


def _preview_payload_from_service_item(item: dict) -> dict:
    variants = item.get("variants") if isinstance(item.get("variants"), list) else []
    normalized_variants = [
        {
            "title": str(variant.get("title") or "").strip(),
            "price": variant.get("price"),
            "compare_at_price": variant.get("compare_at_price"),
            "currency": str(variant.get("currency") or "").strip().upper(),
            "available": bool(variant.get("available", True)),
            "pricing_mode": "source",
        }
        for variant in variants
        if isinstance(variant, dict)
    ]
    orderability_status = str(item.get("orderability_status") or "").strip().lower() or "orderable"
    return {
        "handle": str(item.get("handle") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "description_text": str(item.get("description") or "").strip() or None,
        "description_html": str(item.get("description_html") or "").strip() or None,
        "source_weight_grams": item.get("source_weight_grams"),
        "designer_name": str(item.get("designer") or "").strip() or None,
        "source_category_name": str(item.get("category") or "").strip() or None,
        "gender": str(item.get("gender") or "").strip().lower() or None,
        "product_url": str(item.get("url") or "").strip(),
        "price_summary": ProductQueryService._build_price_summary(normalized_variants),
        "buyer_total_price": item.get("buyer_total_price"),
        "buyer_service_fee": item.get("buyer_service_fee"),
        "visibility_status": "visible",
        "availability_mode": "by_order",
        "orderability_status": orderability_status,
        "image_urls": [str(url).strip() for url in item.get("images") or [] if str(url).strip()],
        "variants": normalized_variants,
    }


def _resolve_source_id(db: Session, url: str) -> int:
    registry = SourceRegistryService(db)
    source_key = registry.normalize_source_key(url)
    source = registry.repo.get_by_key(source_key)
    if source is None:
        raise ValidationError(f"source not found for url: {url}")
    return int(source.id)


@router.get("/products")
def list_products(
    q: str = Query(default="", max_length=255),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    source_id: int | None = Query(default=None),
    source_mode: str | None = Query(default=None),
    designer_id: str | None = Query(default=None),
    gender: str | None = Query(default=None),
    filter_slug: str | None = Query(default=None),
    custom_catalog_slug: str | None = Query(default=None),
    visibility_status: str | None = Query(default=None),
    availability_mode: str | None = Query(default=None),
    orderability_status: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    return ProductQueryService(db).list_products(
        limit=limit,
        offset=offset,
        query=q,
        source_id=source_id,
        source_mode=source_mode,
        designer_filter=designer_id,
        gender=gender,
        filter_slug=filter_slug,
        custom_catalog_slug=custom_catalog_slug,
        visibility_status=visibility_status,
        availability_mode=availability_mode,
        orderability_status=orderability_status,
        audience="public",
    )


@router.get("/admin/products/table", dependencies=[Depends(require_permission("control.products.read"))])
def admin_products_table(
    q: str = Query(default="", max_length=255),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    source_id: int | None = Query(default=None),
    source_mode: str | None = Query(default=None),
    designer_id: str | None = Query(default=None),
    gender: str | None = Query(default=None),
    filter_slug: str | None = Query(default=None),
    custom_catalog_slug: str | None = Query(default=None),
    visibility_status: str | None = Query(default=None),
    availability_mode: str | None = Query(default=None),
    orderability_status: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    return ProductQueryService(db).list_admin_table_products(
        limit=limit,
        offset=offset,
        query=q,
        source_id=source_id,
        source_mode=source_mode,
        designer_filter=designer_id,
        gender=gender,
        filter_slug=filter_slug,
        custom_catalog_slug=custom_catalog_slug,
        visibility_status=visibility_status,
        availability_mode=availability_mode,
        orderability_status=orderability_status,
    )

@router.get("/admin/products/table/facets", dependencies=[Depends(require_permission("control.products.read"))])
def admin_products_table_facets(
    q: str = Query(default="", max_length=255),
    source_id: int | None = Query(default=None),
    source_mode: str | None = Query(default=None),
    designer_id: str | None = Query(default=None),
    gender: str | None = Query(default=None),
    filter_slug: str | None = Query(default=None),
    custom_catalog_slug: str | None = Query(default=None),
    visibility_status: str | None = Query(default=None),
    availability_mode: str | None = Query(default=None),
    orderability_status: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    return ProductQueryService(db).admin_table_facets(
        query=q,
        source_id=source_id,
        source_mode=source_mode,
        designer_filter=designer_id,
        gender=gender,
        filter_slug=filter_slug,
        custom_catalog_slug=custom_catalog_slug,
        visibility_status=visibility_status,
        availability_mode=availability_mode,
        orderability_status=orderability_status,
    )


@router.get("/products/pricing-example", dependencies=[Depends(require_permission("control.pricing.read"))])
def pricing_example(
    product_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    payload = ProductQueryService(db).get_pricing_example_payload(product_id=product_id)
    if payload is None:
        raise NotFoundError("Не удалось выбрать товар для примера")
    return payload


@router.get("/products/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db)) -> dict:
    payload = ProductQueryService(db).get_product_payload(product_id, audience="public")
    if payload is None:
        raise NotFoundError("Товар не найден")
    return payload


@router.get("/admin/products/{product_id}", dependencies=[Depends(require_permission("control.products.read"))])
def get_admin_product(product_id: int, db: Session = Depends(get_db)) -> dict:
    return _get_admin_mutation_payload_or_404(db, product_id)


@router.patch("/products/{product_id}", dependencies=[Depends(require_permission("control.products.edit"))])
def patch_product(product_id: int, payload: ProductPatchRequest, db: Session = Depends(get_db)) -> dict:
    ProductWriteService(db).update_product(
        product_id=product_id,
        payload=payload.model_dump(exclude_unset=True, exclude_none=False),
    )
    db.commit()
    return _get_admin_mutation_payload_or_404(db, product_id)


@router.patch("/admin/products/bulk", dependencies=[Depends(require_permission("control.products.edit"))])
def patch_products_bulk(payload: ProductBulkPatchRequest, db: Session = Depends(get_db)) -> dict:
    updated_ids = ProductWriteService(db).bulk_update_products(
        product_ids=[int(product_id) for product_id in payload.product_ids],
        payload=payload.model_dump(exclude_none=False),
    )
    db.commit()
    return {"ok": True, "updated_product_ids": updated_ids}


@router.post("/products/manual", dependencies=[Depends(require_permission("control.products.edit"))])
def create_manual_product(payload: ManualProductRequest, db: Session = Depends(get_db)) -> dict:
    if payload.bind_source_url:
        service_item = _probe_service_product(payload.bind_source_url)
        source_id = _resolve_source_id(db, payload.bind_source_url)
        product_id = ProductWriteService(db).create_sync_bound_product(
            payload=payload.model_dump(),
            source_id=source_id,
            service_item=service_item,
            force_primary_listing=bool(payload.bind_source_as_primary),
        )
    else:
        product_id = ProductWriteService(db).create_manual_product(payload.model_dump())
    db.commit()
    return {"ok": True, "id": product_id}


@router.patch("/products/manual/{product_id}", dependencies=[Depends(require_permission("control.products.edit"))])
def update_manual_product(product_id: int, payload: ManualProductPatchRequest, db: Session = Depends(get_db)) -> dict:
    ProductWriteService(db).update_manual_product(product_id=product_id, payload=payload.model_dump(exclude_unset=True))
    db.commit()
    return {"ok": True, "id": product_id}


@router.patch("/products/{product_id}/variants", dependencies=[Depends(require_permission("control.products.edit"))])
def update_product_variants(product_id: int, payload: ManualVariantsPatchRequest, db: Session = Depends(get_db)) -> dict:
    ProductWriteService(db).update_manual_variants(product_id=product_id, variants=payload.variants)
    db.commit()
    return {"ok": True, "product": _get_admin_mutation_payload_or_404(db, product_id)}


@router.delete("/products/manual/{product_id}", dependencies=[Depends(require_permission("control.products.edit"))])
def delete_manual_product(product_id: int, db: Session = Depends(get_db)) -> dict:
    ProductWriteService(db).delete_manual_product(product_id=product_id)
    db.commit()
    return {"ok": True}


@router.post("/products/upload-image", dependencies=[Depends(require_permission("control.products.edit"))])
def upload_product_image(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    asset = MediaAssetService(db).save_upload(scope="products", upload=file)
    db.commit()
    return {"ok": True, "image_asset_id": int(asset.id)}


@router.post("/products/upload-image-by-url", dependencies=[Depends(require_permission("control.products.edit"))])
def upload_product_image_by_url(payload: ProductUrlRequest, db: Session = Depends(get_db)) -> dict:
    asset = MediaAssetService(db).save_from_url(scope="products", url=payload.url)
    db.commit()
    return {"ok": True, "image_asset_id": int(asset.id)}


@router.get("/products/images/{image_id}")
def get_product_image(image_id: int, db: Session = Depends(get_db)) -> FileResponse:
    asset = db.query(ImageAsset).filter(ImageAsset.id == int(image_id)).one_or_none()
    if asset is None:
        raise NotFoundError("Изображение не найдено")
    return FileResponse(MediaAssetService(db).resolve_file_path(asset), media_type=asset.mime_type)


@router.post("/products/preview-by-url", dependencies=[Depends(require_permission("control.products.edit"))])
def preview_product_by_url(payload: ProductUrlRequest) -> dict:
    return _preview_payload_from_service_item(_probe_service_product(payload.url))


@router.post("/products/probe-by-url", dependencies=[Depends(require_permission("control.products.edit"))])
def probe_product_by_url(payload: ProductUrlRequest) -> dict:
    return _preview_payload_from_service_item(_probe_service_product(payload.url))


@router.post("/products/add-by-url", dependencies=[Depends(require_permission("control.products.edit"))])
def add_product_by_url(payload: ProductUrlRequest, db: Session = Depends(get_db)) -> dict:
    service_item = _probe_service_product(payload.url)
    source_id = _resolve_source_id(db, payload.url)
    ProductIngestService(db).apply_batch(source_id=source_id, items=[service_item])
    DesignerCatalogSyncService(db).reconcile(sync_product_links=True)
    db.commit()
    return {"ok": True}


@router.post("/products/{product_id}/bind-source-by-url", dependencies=[Depends(require_permission("control.products.edit"))])
def bind_source_by_url(product_id: int, payload: BindSourceByUrlRequest, db: Session = Depends(get_db)) -> dict:
    service_item = _probe_service_product(payload.url)
    source_id = _resolve_source_id(db, payload.url)
    ProductIngestService(db).apply_batch(
        source_id=source_id,
        items=[service_item],
        target_product_id=product_id,
        force_primary_listing=bool(payload.set_as_primary),
    )
    DesignerCatalogSyncService(db).reconcile(sync_product_links=True)
    db.commit()
    return _get_admin_mutation_payload_or_404(db, product_id)


@router.delete("/products/{product_id}/listings/{listing_id}", dependencies=[Depends(require_permission("control.products.edit"))])
def unbind_listing(product_id: int, listing_id: int, db: Session = Depends(get_db)) -> dict:
    detached_product_id = ProductWriteService(db).unbind_listing(product_id=product_id, listing_id=listing_id)
    db.commit()
    return {
        "ok": True,
        "product": _get_admin_mutation_payload_or_404(db, product_id),
        "detached_product_id": detached_product_id,
    }
