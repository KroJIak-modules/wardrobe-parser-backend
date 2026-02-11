from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.product_repository import ProductRepository
from app.repositories.site_repository import SiteRepository
from app.schemas.common import CursorPage
from app.schemas.product import ProductCreate, ProductResponse, ProductUpdate
from app.services.product_service import ProductService

router = APIRouter()


@router.get("/", response_model=CursorPage[ProductResponse], summary="List products")
def list_products(
    site_key: str = Query(..., description="Site key"),
    cursor_id: int | None = Query(default=None, description="Cursor id"),
    limit: int = Query(default=50, ge=1, le=200),
    filter_key: str | None = Query(default=None, description="Field to filter"),
    filter_value: str | None = Query(default=None, description="Filter value"),
    db: Session = Depends(get_db),
) -> CursorPage[ProductResponse]:
    site = SiteRepository.get_by_key(db, site_key)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    items, next_cursor = ProductRepository.list_by_site(
        db,
        site.id,
        cursor_id,
        limit,
        filter_key,
        filter_value,
    )
    return CursorPage(
        items=[ProductResponse.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )


@router.get("/{product_id}", response_model=ProductResponse, summary="Get product")
def get_product(product_id: int, db: Session = Depends(get_db)) -> ProductResponse:
    product = ProductRepository.get_by_id(db, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return ProductResponse.model_validate(product)


@router.post("/", response_model=ProductResponse, status_code=201, summary="Create product")
def create_product(payload: ProductCreate, db: Session = Depends(get_db)) -> ProductResponse:
    product = ProductRepository.create(db, **payload.model_dump())
    ProductService.mark_user_update(db, product)
    db.commit()
    return ProductResponse.model_validate(product)


@router.put("/{product_id}", response_model=ProductResponse, summary="Update product")
def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
) -> ProductResponse:
    product = ProductRepository.get_by_id(db, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    updated = ProductRepository.update(db, product, **payload.model_dump(exclude_none=True))
    ProductService.mark_user_update(db, updated)
    db.commit()
    return ProductResponse.model_validate(updated)


@router.delete("/{product_id}", status_code=204, summary="Delete product")
def delete_product(product_id: int, db: Session = Depends(get_db)) -> None:
    product = ProductRepository.get_by_id(db, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    ProductRepository.soft_delete(db, product)
    db.commit()


