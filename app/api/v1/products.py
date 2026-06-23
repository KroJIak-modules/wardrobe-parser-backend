from __future__ import annotations

from pathlib import Path
import time
import requests
from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import NotFoundError, ValidationError
from app.models import ImageAsset, Product, ProductListing, ProductListingMember
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.media_asset_service import MediaAssetService
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.product_query_service import ProductQueryService
from app.services.catalog.product_write_service import ProductWriteService
from app.services.catalog.source_registry_service import SourceRegistryService


router = APIRouter(tags=["products"])


class PriceOverridePatchRequest(BaseModel):
    manual_price_rub: float | None = None
    manual_compare_at_price_rub: float | None = None

    @model_validator(mode="after")
    def validate_manual_price_present(self) -> "PriceOverridePatchRequest":
        if self.manual_price_rub is None:
            raise ValueError("manual_price_rub is required when price_override is provided")
        return self


class ProductPatchRequest(BaseModel):
    title_override: str | None = None
    description_text: str | None = None
    description_html: str | None = None
    description_visibility: bool | None = None
    visibility_status: str | None = None
    availability_mode: str | None = None
    gender: str | None = None
    primary_listing_id: int | None = None
    manual_weight_grams: int | None = None
    price_override: PriceOverridePatchRequest | None = None
    images: dict | None = None
    gallery_listing_id: int | None = None
    filter_slugs: list[str] | None = None
    custom_catalog_slugs: list[str] | None = None
    reset_to_default: list[str] = Field(default_factory=list)


class ProductBulkPatchRequest(BaseModel):
    product_ids: list[int] = Field(default_factory=list, min_length=1)
    gender: str | None = None


class ManualVariantRequest(BaseModel):
    title: str
    price: float | None = None
    currency: str = "RUB"
    available: bool = True


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
    price_override: PriceOverridePatchRequest | None = None
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
    price_override: PriceOverridePatchRequest | None = None
    filter_slugs: list[str] | None = None
    custom_catalog_slugs: list[str] | None = None


class ProductUrlRequest(BaseModel):
    url: str


class BindSourceByUrlRequest(BaseModel):
    url: str
    set_as_primary: bool = False


def _service_url(path: str) -> str:
    return f"{settings.service_base_url.rstrip('/')}/api/v1/sync{path}"


def _probe_service_product(url: str) -> dict:
    create_res = requests.post(_service_url("/probe/jobs"), json={"product_url": url, "dry_run": True}, timeout=(5, 30))
    create_res.raise_for_status()
    create_payload = create_res.json() if isinstance(create_res.json(), dict) else {}
    job_id = str(create_payload.get("job_id") or "").strip()
    if not job_id:
        raise ValidationError("service probe did not return job_id")

    for _ in range(60):
        status_res = requests.get(_service_url(f"/probe/jobs/{job_id}"), timeout=(5, 20))
        status_res.raise_for_status()
        status_payload = status_res.json() if isinstance(status_res.json(), dict) else {}
        status_value = str(status_payload.get("status") or "").strip().lower()
        if status_value in {"completed", "failed", "canceled"}:
            break
        time.sleep(1.0)

    events_res = requests.get(_service_url(f"/probe/jobs/{job_id}/events"), params={"cursor": 0, "limit": 500}, timeout=(5, 20))
    events_res.raise_for_status()
    events_payload = events_res.json() if isinstance(events_res.json(), dict) else {}
    for event in events_payload.get("items") if isinstance(events_payload.get("items"), list) else []:
        if not isinstance(event, dict):
            continue
        if str(event.get("type") or "").strip() != "product_batch":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        for item in items:
            if isinstance(item, dict) and str(item.get("url") or "").strip():
                return item
    raise NotFoundError("Товар по URL не найден")


def _preview_payload_from_service_item(item: dict) -> dict:
    variants = item.get("variants") if isinstance(item.get("variants"), list) else []
    first_variant = next((variant for variant in variants if isinstance(variant, dict)), None)
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
        "price": first_variant.get("price") if first_variant else None,
        "currency": str(first_variant.get("currency") or "").strip().upper() if first_variant else "",
        "buyer_total_price": item.get("buyer_total_price"),
        "buyer_service_fee": item.get("buyer_service_fee"),
        "visibility_status": "visible",
        "availability_mode": "by_order",
        "orderability_status": orderability_status,
        "image_urls": [str(url).strip() for url in item.get("images") or [] if str(url).strip()],
        "variants": [
            {
                "title": str(variant.get("title") or "").strip(),
                "price": variant.get("price"),
                "currency": str(variant.get("currency") or "").strip().upper(),
                "available": bool(variant.get("available", True)),
            }
            for variant in variants
            if isinstance(variant, dict)
        ],
    }


def _resolve_source_id(db: Session, url: str) -> int:
    registry = SourceRegistryService(db)
    registry.refresh_from_service()
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
def pricing_example(db: Session = Depends(get_db)) -> dict:
    payload = ProductQueryService(db).get_pricing_example_payload()
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
    payload = ProductQueryService(db).get_product_payload(product_id, audience="admin")
    if payload is None:
        raise NotFoundError("Товар не найден")
    return payload


@router.patch("/products/{product_id}", dependencies=[Depends(require_permission("control.products.edit"))])
def patch_product(product_id: int, payload: ProductPatchRequest, db: Session = Depends(get_db)) -> dict:
    ProductWriteService(db).update_product(
        product_id=product_id,
        payload=payload.model_dump(exclude_unset=True, exclude_none=False),
    )
    db.commit()
    refreshed = ProductQueryService(db).get_product_payload(product_id, audience="admin")
    if refreshed is None:
        raise NotFoundError("Товар не найден")
    return refreshed


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
    product_id = ProductWriteService(db).create_manual_product(payload.model_dump())
    if payload.bind_source_url:
        service_item = _probe_service_product(payload.bind_source_url)
        source_id = _resolve_source_id(db, payload.bind_source_url)
        ProductIngestService(db).apply_batch(
            source_id=source_id,
            items=[service_item],
            target_product_id=product_id,
            force_primary_listing=bool(payload.bind_source_as_primary),
        )
    db.commit()
    return {"ok": True, "id": product_id}


@router.patch("/products/manual/{product_id}", dependencies=[Depends(require_permission("control.products.edit"))])
def update_manual_product(product_id: int, payload: ManualProductPatchRequest, db: Session = Depends(get_db)) -> dict:
    ProductWriteService(db).update_manual_product(product_id=product_id, payload=payload.model_dump(exclude_unset=True))
    db.commit()
    return {"ok": True, "id": product_id}


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
    db.commit()
    refreshed = ProductQueryService(db).get_product_payload(product_id, audience="admin")
    if refreshed is None:
        raise NotFoundError("Товар не найден")
    return refreshed


@router.delete("/products/{product_id}/listings/{listing_id}", dependencies=[Depends(require_permission("control.products.edit"))])
def unbind_listing(product_id: int, listing_id: int, db: Session = Depends(get_db)) -> dict:
    detached_product_id = ProductWriteService(db).unbind_listing(product_id=product_id, listing_id=listing_id)
    db.commit()
    refreshed = ProductQueryService(db).get_product_payload(product_id, audience="admin")
    if refreshed is None:
        raise NotFoundError("Товар не найден")
    return {
        "ok": True,
        "product": refreshed,
        "detached_product_id": detached_product_id,
    }
