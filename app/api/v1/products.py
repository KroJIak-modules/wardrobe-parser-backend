from __future__ import annotations

from pathlib import Path
import time
import requests
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
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
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.product_query_service import ProductQueryService
from app.services.catalog.product_write_service import ProductWriteService
from app.services.catalog.source_registry_service import SourceRegistryService


router = APIRouter(tags=["products"])


class ProductPatchRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    description_visible: bool | None = None
    status: str | None = None
    images: dict | None = None
    reset_to_default: list[str] = Field(default_factory=list)


class ManualVariantRequest(BaseModel):
    title: str
    price: float | None = None
    currency: str = "RUB"
    available: bool = True


class ManualProductRequest(BaseModel):
    title: str
    description: str | None = None
    vendor: str | None = None
    product_type: str | None = None
    variants: list[ManualVariantRequest] = Field(default_factory=list)
    manual_image_asset_ids: list[int] = Field(default_factory=list)
    weight_grams: int | None = None
    status: str | None = None


class ProductUrlRequest(BaseModel):
    url: str


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
        if status_value in {"completed", "failed", "cancelled"}:
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
    return {
        "handle": str(item.get("handle") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "description": str(item.get("description") or "").strip() or None,
        "weight_grams": item.get("weight_grams"),
        "vendor": str(item.get("designer") or "").strip() or None,
        "product_type": str(item.get("category") or "").strip() or None,
        "product_url": str(item.get("url") or "").strip(),
        "price": first_variant.get("price") if first_variant else None,
        "currency": str(first_variant.get("currency") or "").strip().upper() if first_variant else "",
        "buyer_total_price": item.get("buyer_total_price"),
        "buyer_service_fee": item.get("buyer_service_fee"),
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
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    source_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    payload = ProductQueryService(db).list_products(limit=limit, offset=offset)
    if source_id is None:
        return payload
    filtered = [
        item
        for item in payload["items"]
        if int(item.get("source_id") or 0) == int(source_id)
    ]
    return {
        "items": filtered,
        "total": len(filtered),
        "limit": limit,
        "offset": offset,
    }


@router.get("/admin/products/table", dependencies=[Depends(require_permission("control.products.read"))])
def admin_products_table(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    source_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    return list_products(limit=limit, offset=offset, source_id=source_id, db=db)


@router.get("/admin/products/table/facets", dependencies=[Depends(require_permission("control.products.read"))])
def admin_products_table_facets(db: Session = Depends(get_db)) -> dict:
    rows = (
        db.query(ProductListing.source_id, func.count(func.distinct(Product.id)))
        .join(ProductListingMember, ProductListingMember.listing_id == ProductListing.id)
        .join(Product, Product.id == ProductListingMember.product_id)
        .filter(Product.lifecycle_status != "merged")
        .group_by(ProductListing.source_id)
        .all()
    )
    return {"source_counts": [{"source_id": int(source_id), "count": int(count)} for source_id, count in rows]}


@router.get("/products/pricing-example", dependencies=[Depends(require_permission("control.pricing.read"))])
def pricing_example(db: Session = Depends(get_db)) -> dict:
    payload = ProductQueryService(db).list_products(limit=1, offset=0)
    if not payload["items"]:
        raise NotFoundError("Нет товаров для примера")
    product = payload["items"][0]
    return {
        "product_id": int(product["id"]),
        "title": product["title"],
        "url": product["url"],
        "source_name": product.get("source_name"),
        "image_url": (product.get("image_urls") or [None])[0],
        "source_price": product.get("source_price"),
        "source_currency": product.get("source_currency"),
        "final_price": product.get("final_price"),
        "components": product.get("pricing_components") or {},
    }


@router.get("/products/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db)) -> dict:
    payload = ProductQueryService(db).get_product_payload(product_id)
    if payload is None:
        raise NotFoundError("Товар не найден")
    return payload


@router.patch("/products/{product_id}", dependencies=[Depends(require_permission("control.products.edit"))])
def patch_product(product_id: int, payload: ProductPatchRequest, db: Session = Depends(get_db)) -> dict:
    ProductWriteService(db).update_product(product_id=product_id, payload=payload.model_dump(exclude_none=False))
    db.commit()
    refreshed = ProductQueryService(db).get_product_payload(product_id)
    if refreshed is None:
        raise NotFoundError("Товар не найден")
    return refreshed


@router.post("/products/manual", dependencies=[Depends(require_permission("control.products.edit"))])
def create_manual_product(payload: ManualProductRequest, db: Session = Depends(get_db)) -> dict:
    product_id = ProductWriteService(db).create_manual_product(payload.model_dump())
    db.commit()
    return {"ok": True, "id": product_id}


@router.patch("/products/manual/{product_id}", dependencies=[Depends(require_permission("control.products.edit"))])
def update_manual_product(product_id: int, payload: ManualProductRequest, db: Session = Depends(get_db)) -> dict:
    ProductWriteService(db).update_manual_product(product_id=product_id, payload=payload.model_dump())
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


@router.get("/products/{product_id}/starred-categories", dependencies=[Depends(require_permission("showcase.read"))])
def get_product_starred_categories(product_id: int) -> dict:
    return {"assigned_category_ids": [], "available_categories": []}


class StarredCategoriesRequest(BaseModel):
    category_ids: list[int] = Field(default_factory=list)


@router.put("/products/{product_id}/starred-categories", dependencies=[Depends(require_permission("showcase.edit"))])
def put_product_starred_categories(product_id: int, payload: StarredCategoriesRequest) -> dict:
    return {"assigned_category_ids": [int(item) for item in payload.category_ids]}


@router.get("/products/starred-categories/options", dependencies=[Depends(require_permission("showcase.read"))])
def get_starred_categories_options() -> dict:
    return {"items": []}
