"""API endpoints for recursive category tree and keyword rules."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.parser import (
    CategoryCreateRequest,
    CategoryKeywordRequest,
    CategoryTreeNodeResponse,
    CategoryUpdateRequest,
)
from app.services.catalog.category_tree_service import CategoryTreeService

router = APIRouter(tags=["categories"])


@router.get("/categories/tree", response_model=list[CategoryTreeNodeResponse])
def get_category_tree(db: Session = Depends(get_db)):
    return CategoryTreeService(db).get_category_tree()


@router.post("/categories", response_model=CategoryTreeNodeResponse)
def create_category(payload: CategoryCreateRequest, db: Session = Depends(get_db)):
    return CategoryTreeService(db).create_category(payload)


@router.patch("/categories/{category_id}", response_model=CategoryTreeNodeResponse)
def update_category(category_id: int, payload: CategoryUpdateRequest, db: Session = Depends(get_db)):
    return CategoryTreeService(db).update_category(category_id, payload)


@router.delete("/categories/{category_id}")
def delete_category(category_id: int, db: Session = Depends(get_db)):
    return CategoryTreeService(db).delete_category(category_id)


@router.post("/categories/{category_id}/keywords")
def add_category_keyword(category_id: int, payload: CategoryKeywordRequest, db: Session = Depends(get_db)):
    return CategoryTreeService(db).add_category_keyword(category_id, payload)


@router.delete("/categories/{category_id}/keywords/{keyword}")
def remove_category_keyword(category_id: int, keyword: str, db: Session = Depends(get_db)):
    return CategoryTreeService(db).remove_category_keyword(category_id, keyword)
