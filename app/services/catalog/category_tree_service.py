"""Service layer for category tree and keyword rule operations."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.repositories import (
    ParserCategoryKeywordRepository,
    ParserCategoryRepository,
    ParserFavoriteProductRepository,
    ParserProductRepository,
)
from app.schemas.parser import CategoryCreateRequest, CategoryKeywordRequest, CategoryTreeNodeResponse, CategoryUpdateRequest
from app.services.catalog.category_assignment import CategoryAssigner, aggregate_tree_counts
from app.services.catalog.category_tree_rules import (
    build_unique_slug,
    ensure_fallback,
    ensure_favorite,
    normalize_keyword,
    purge_favorite_keywords,
    purge_fallback_keywords,
    validate_parent_for_create,
    validate_parent_for_update,
)
from app.services.catalog.category_tree_utils import build_single_node_response, build_tree, find_node


class CategoryTreeService:
    def __init__(self, db: Session):
        self.db = db
        self.category_repo = ParserCategoryRepository(db)
        self.keyword_repo = ParserCategoryKeywordRepository(db)
        self.favorite_repo = ParserFavoriteProductRepository(db)
        self.product_repo = ParserProductRepository(db)

    def get_category_tree(self) -> list[CategoryTreeNodeResponse]:
        fallback = ensure_fallback(self.category_repo)
        favorite = ensure_favorite(self.category_repo)
        purge_fallback_keywords(db=self.db, keyword_repo=self.keyword_repo, fallback=fallback)
        purge_favorite_keywords(db=self.db, keyword_repo=self.keyword_repo, favorite=favorite)
        self.db.commit()

        categories = self.category_repo.get_all_active()
        base_tree = build_tree(categories, self.keyword_repo)
        assigner = CategoryAssigner(base_tree)
        products = self.product_repo.list_active_for_category_counts()
        favorite_product_ids = self.favorite_repo.get_product_id_set()
        direct_counts = assigner.direct_counts(products, favorite_product_ids)
        aggregated_counts = aggregate_tree_counts(base_tree, direct_counts)
        return build_tree(categories, self.keyword_repo, product_counts=aggregated_counts)

    def create_category(self, payload: CategoryCreateRequest) -> CategoryTreeNodeResponse:
        ensure_fallback(self.category_repo)
        ensure_favorite(self.category_repo)
        validate_parent_for_create(category_repo=self.category_repo, parent_id=payload.parent_id)
        slug = build_unique_slug(name=payload.name, category_repo=self.category_repo)
        category = self.category_repo.create(
            name=payload.name.strip(),
            slug=slug,
            parent_id=payload.parent_id,
            is_fallback=False,
            is_favorite=False,
        )
        self.category_repo.flush()
        self.db.commit()
        categories = self.category_repo.get_all_active()
        tree = build_tree(categories, self.keyword_repo)
        created_node = find_node(tree, category.id)
        return created_node or build_single_node_response(category, self.keyword_repo)

    def update_category(self, category_id: int, payload: CategoryUpdateRequest) -> CategoryTreeNodeResponse:
        category = self.category_repo.get_by_id(category_id)
        if not category or category.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Категория не найдена")

        if payload.name is not None:
            category.name = payload.name.strip()
            if not category.is_fallback:
                category.slug = build_unique_slug(
                    name=category.name,
                    category_repo=self.category_repo,
                    exclude_category_id=category.id,
                )

        if "parent_id" in payload.model_fields_set:
            if (category.is_fallback or category.is_favorite) and payload.parent_id is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Системную категорию нельзя делать дочерней")
            validate_parent_for_update(category=category, parent_id=payload.parent_id, category_repo=self.category_repo)

        self.db.commit()
        categories = self.category_repo.get_all_active()
        tree = build_tree(categories, self.keyword_repo)
        updated_node = find_node(tree, category.id)
        return updated_node or build_single_node_response(category, self.keyword_repo)

    def delete_category(self, category_id: int) -> dict:
        category = self.category_repo.get_by_id(category_id)
        if not category or category.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Категория не найдена")
        if category.is_fallback:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Категорию 'Прочее' нельзя удалить")
        if category.is_favorite:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Категорию 'Избранное' нельзя удалить")
        if self.category_repo.get_children(category_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Нельзя удалить категорию с дочерними категориями")

        category.deleted_at = datetime.now(timezone.utc)
        self.db.commit()
        return {"ok": True}

    def add_category_keyword(self, category_id: int, payload: CategoryKeywordRequest) -> dict:
        category = self.category_repo.get_by_id(category_id)
        if not category or category.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Категория не найдена")
        if category.is_fallback or category.is_favorite:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="У системной категории не может быть ключевых слов")

        keyword = normalize_keyword(payload.keyword)
        existing = self.keyword_repo.get_exact(category_id, keyword)
        if existing:
            return {"ok": True, "keyword": keyword, "duplicated": True}
        self.keyword_repo.create(category_id=category_id, keyword=keyword)
        self.db.commit()
        return {"ok": True, "keyword": keyword}

    def remove_category_keyword(self, category_id: int, keyword: str) -> dict:
        category = self.category_repo.get_by_id(category_id)
        if not category or category.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Категория не найдена")
        if category.is_fallback or category.is_favorite:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="У системной категории нет управляемых ключей")

        normalized = keyword.strip().lower()
        entity = self.keyword_repo.get_exact(category_id, normalized)
        if not entity:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ключевое слово не найдено")
        self.db.delete(entity)
        self.db.commit()
        return {"ok": True}
