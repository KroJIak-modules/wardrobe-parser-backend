"""Service layer for category tree and keyword rule operations."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import ParserCategory, ParserCategoryKeyword, ParserProduct, ParserSource
from app.repositories import (
    ParserCategoryKeywordRepository,
    ParserCategoryManualProductRepository,
    ParserCategoryRepository,
    ParserProductRepository,
)
from app.schemas.parser import (
    CategoryCreateRequest,
    CategoryKeywordRequest,
    CategoryManualProductResponse,
    CategoryTreeNodeResponse,
    CategoryUpdateRequest,
)
from app.services.catalog.category_assignment import CategoryAssigner, aggregate_tree_counts
from app.services.catalog.category_tree_rules import (
    build_unique_slug,
    ensure_fallback,
    normalize_keyword,
    validate_parent_for_create,
    validate_parent_for_update,
)
from app.services.catalog.category_tree_utils import build_single_node_response, build_tree, find_node


class CategoryTreeService:
    DESIGNERS_ROOT_NAME = "Дизайнеры"
    DESIGNERS_ROOT_SLUG = "dizaynery"
    DESIGNERS_SYNC_INTERVAL_SEC = 300
    _last_designers_sync_at: datetime | None = None

    def __init__(self, db: Session):
        self.db = db
        self.category_repo = ParserCategoryRepository(db)
        self.keyword_repo = ParserCategoryKeywordRepository(db)
        self.manual_product_repo = ParserCategoryManualProductRepository(db)
        self.product_repo = ParserProductRepository(db)

    def _build_tree(self, categories: list[ParserCategory], designers_root: ParserCategory | None) -> list[CategoryTreeNodeResponse]:
        designers_root_id = designers_root.id if designers_root is not None else None
        return build_tree(
            categories,
            self.keyword_repo,
            designers_root_id=designers_root_id,
        )

    def _build_tree_with_counts(self, categories: list[ParserCategory], designers_root: ParserCategory | None) -> list[CategoryTreeNodeResponse]:
        designers_root_id = designers_root.id if designers_root is not None else None
        base_tree = self._build_tree(categories, designers_root)
        products = self.product_repo.list_active_for_category_counts()
        product_ids = {int(item.id) for item in products}
        manual_map = self.manual_product_repo.get_grouped_by_product_ids(product_ids)
        assigner = CategoryAssigner(base_tree, manual_category_ids_by_product=manual_map)
        direct_counts = assigner.direct_counts(products)
        aggregated_counts = aggregate_tree_counts(base_tree, direct_counts)
        return build_tree(
            categories,
            self.keyword_repo,
            product_counts=aggregated_counts,
            designers_root_id=designers_root_id,
        )

    @staticmethod
    def _normalize_name(value: str) -> str:
        return value.strip().casefold()

    @classmethod
    def _should_sync_designers(cls) -> bool:
        now = datetime.now(timezone.utc)
        last = cls._last_designers_sync_at
        if last is None:
            return True
        return (now - last).total_seconds() >= cls.DESIGNERS_SYNC_INTERVAL_SEC

    @classmethod
    def _mark_designers_synced(cls) -> None:
        cls._last_designers_sync_at = datetime.now(timezone.utc)

    def _find_designers_root(self, categories: list[ParserCategory]) -> ParserCategory | None:
        by_name = [
            item
            for item in categories
            if item.deleted_at is None
            and item.parent_id is None
            and self._normalize_name(item.name) == self._normalize_name(self.DESIGNERS_ROOT_NAME)
        ]
        if by_name:
            by_name.sort(key=lambda item: int(item.id))
            return by_name[0]

        by_slug = [
            item
            for item in categories
            if item.deleted_at is None and item.parent_id is None and str(item.slug) == self.DESIGNERS_ROOT_SLUG
        ]
        if by_slug:
            by_slug.sort(key=lambda item: int(item.id))
            return by_slug[0]
        return None

    def _collect_subtree_ids(self, categories: list[ParserCategory], root_id: int) -> set[int]:
        by_parent: dict[int | None, list[int]] = {}
        for item in categories:
            if item.deleted_at is not None:
                continue
            by_parent.setdefault(item.parent_id, []).append(int(item.id))

        result: set[int] = set()
        stack = [int(root_id)]
        while stack:
            current = stack.pop()
            if current in result:
                continue
            result.add(current)
            stack.extend(by_parent.get(current, []))
        return result

    def _soft_delete_subtree(self, categories: list[ParserCategory], root_id: int) -> bool:
        to_delete = self._collect_subtree_ids(categories, root_id)
        if not to_delete:
            return False
        now = datetime.now(timezone.utc)
        changed = False
        by_id = {int(item.id): item for item in categories}
        for category_id in to_delete:
            node = by_id.get(int(category_id))
            if not node:
                continue
            if node.is_fallback:
                continue
            if node.deleted_at is None:
                node.deleted_at = now
                changed = True
        return changed

    def _purge_keywords(self, category_ids: set[int]) -> bool:
        if not category_ids:
            return False
        deleted = (
            self.db.query(ParserCategoryKeyword)
            .filter(ParserCategoryKeyword.category_id.in_(list(category_ids)))
            .delete(synchronize_session=False)
        )
        return bool(deleted)

    def _sync_designers_branch(self, designers_root: ParserCategory, categories: list[ParserCategory]) -> bool:
        changed = False
        root_id = int(designers_root.id)
        vendors = self.product_repo.list_distinct_vendors()
        vendor_by_key: dict[str, str] = {}
        for vendor in vendors:
            key = vendor.casefold().strip()
            if not key:
                continue
            vendor_by_key.setdefault(key, vendor)

        direct_children = [item for item in categories if item.deleted_at is None and item.parent_id == root_id]
        direct_children.sort(key=lambda item: int(item.id))

        # Deduplicate manual duplicates in the designers list.
        seen_child_keys: set[str] = set()
        for child in direct_children:
            key = str(child.name or "").strip().casefold()
            if not key:
                if self._soft_delete_subtree(categories, int(child.id)):
                    changed = True
                continue
            if key in seen_child_keys:
                if self._soft_delete_subtree(categories, int(child.id)):
                    changed = True
                continue
            seen_child_keys.add(key)

        if changed:
            categories = self.category_repo.get_all_active()
            direct_children = [item for item in categories if item.deleted_at is None and item.parent_id == root_id]
            direct_children.sort(key=lambda item: int(item.id))

        existing_by_key: dict[str, ParserCategory] = {}
        for child in direct_children:
            key = str(child.name or "").strip().casefold()
            if key:
                existing_by_key[key] = child

        for key, display_name in vendor_by_key.items():
            existing = existing_by_key.get(key)
            if existing is None:
                slug = build_unique_slug(name=display_name, category_repo=self.category_repo)
                self.category_repo.create(
                    name=display_name,
                    slug=slug,
                    parent_id=root_id,
                    is_fallback=False,
                    is_favorite=False,
                    is_enabled=True,
                )
                changed = True
                continue
            if existing.name != display_name:
                existing.name = display_name
                changed = True

        valid_keys = set(vendor_by_key.keys())
        for child in direct_children:
            key = str(child.name or "").strip().casefold()
            if key in valid_keys:
                continue
            if self._soft_delete_subtree(categories, int(child.id)):
                changed = True

        if changed:
            categories = self.category_repo.get_all_active()

        designers_branch_ids = self._collect_subtree_ids(categories, root_id)
        if self._purge_keywords(designers_branch_ids):
            changed = True

        return changed

    def _prepare_categories(self, *, sync_designers: bool) -> tuple[list[ParserCategory], ParserCategory | None, set[int]]:
        changed = False
        fallback = ensure_fallback(self.category_repo)

        # Track cleanup changes for fallback explicitly.
        if self._purge_keywords({int(fallback.id)}):
            changed = True

        categories = self.category_repo.get_all_active()
        designers_root = self._find_designers_root(categories)
        if designers_root is None:
            slug = build_unique_slug(name=self.DESIGNERS_ROOT_NAME, category_repo=self.category_repo)
            designers_root = self.category_repo.create(
                name=self.DESIGNERS_ROOT_NAME,
                slug=slug,
                parent_id=None,
                is_fallback=False,
                is_favorite=False,
                is_enabled=True,
            )
            self.category_repo.flush()
            changed = True
            categories = self.category_repo.get_all_active()
        else:
            if designers_root.parent_id is not None:
                designers_root.parent_id = None
                changed = True
            if designers_root.name != self.DESIGNERS_ROOT_NAME:
                designers_root.name = self.DESIGNERS_ROOT_NAME
                changed = True

        if sync_designers and designers_root is not None:
            if self._sync_designers_branch(designers_root, categories):
                changed = True
                categories = self.category_repo.get_all_active()

        if changed:
            self.db.commit()
            categories = self.category_repo.get_all_active()
            designers_root = self._find_designers_root(categories)

        designers_branch_ids: set[int] = set()
        if designers_root is not None:
            designers_branch_ids = self._collect_subtree_ids(categories, int(designers_root.id))
        return categories, designers_root, designers_branch_ids

    @staticmethod
    def _assert_keywords_editable(category: ParserCategory, has_children: bool, in_designers_branch: bool) -> None:
        if category.is_fallback or in_designers_branch:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="У системной категории ключевые слова недоступны",
            )
        if has_children:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ключевые слова можно редактировать только у конечной категории без дочерних веток",
            )

    def get_category_tree(self, *, include_counts: bool = True) -> list[CategoryTreeNodeResponse]:
        sync_designers = self._should_sync_designers()
        categories, designers_root, _ = self._prepare_categories(sync_designers=sync_designers)
        if sync_designers:
            self._mark_designers_synced()
        if include_counts:
            return self._build_tree_with_counts(categories, designers_root)
        return self._build_tree(categories, designers_root)

    def create_category(self, payload: CategoryCreateRequest) -> CategoryTreeNodeResponse:
        categories, designers_root, designers_branch_ids = self._prepare_categories(sync_designers=False)
        validate_parent_for_create(category_repo=self.category_repo, parent_id=payload.parent_id)
        if payload.parent_id is not None:
            parent = self.category_repo.get_by_id(payload.parent_id)
            if parent is not None and (parent.is_fallback or int(parent.id) in designers_branch_ids):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Создание дочерних категорий в системной ветке запрещено",
                )
        slug = build_unique_slug(name=payload.name, category_repo=self.category_repo)
        category = self.category_repo.create(
            name=payload.name.strip(),
            slug=slug,
            parent_id=payload.parent_id,
            is_fallback=False,
            is_favorite=False,
            is_enabled=True,
        )
        self.category_repo.flush()
        self.db.commit()
        categories = self.category_repo.get_all_active()
        tree = self._build_tree_with_counts(categories, designers_root)
        created_node = find_node(tree, category.id)
        return created_node or build_single_node_response(category, self.keyword_repo)

    def update_category(self, category_id: int, payload: CategoryUpdateRequest) -> CategoryTreeNodeResponse:
        categories, designers_root, designers_branch_ids = self._prepare_categories(sync_designers=False)
        category = self.category_repo.get_by_id(category_id)
        if not category or category.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Категория не найдена")
        in_designers_branch = int(category.id) in designers_branch_ids
        is_system = bool(category.is_fallback) or in_designers_branch

        if payload.name is not None:
            if is_system:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Системную категорию нельзя переименовать")
            category.name = payload.name.strip()
            if not category.is_fallback:
                category.slug = build_unique_slug(
                    name=category.name,
                    category_repo=self.category_repo,
                    exclude_category_id=category.id,
                )

        if "parent_id" in payload.model_fields_set:
            if is_system:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Системную категорию нельзя перемещать в дереве")
            if payload.parent_id is not None:
                parent = self.category_repo.get_by_id(payload.parent_id)
                if parent is not None and (parent.is_fallback or int(parent.id) in designers_branch_ids):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Нельзя переместить категорию в системную ветку",
                    )
            if category.is_fallback and payload.parent_id is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Системную категорию нельзя делать дочерней")
            validate_parent_for_update(category=category, parent_id=payload.parent_id, category_repo=self.category_repo)

        if "is_enabled" in payload.model_fields_set and payload.is_enabled is not None:
            category.is_enabled = bool(payload.is_enabled)

        if "is_favorite" in payload.model_fields_set and payload.is_favorite is not None:
            if category.is_fallback or in_designers_branch:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Системную категорию нельзя отмечать звездой")
            category.is_favorite = bool(payload.is_favorite)

        self.db.commit()
        categories = self.category_repo.get_all_active()
        tree = self._build_tree_with_counts(categories, designers_root)
        updated_node = find_node(tree, category.id)
        return updated_node or build_single_node_response(category, self.keyword_repo)

    def delete_category(self, category_id: int) -> dict:
        _, _, designers_branch_ids = self._prepare_categories(sync_designers=False)
        category = self.category_repo.get_by_id(category_id)
        if not category or category.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Категория не найдена")
        if category.is_fallback:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Категорию 'Прочее' нельзя удалить")
        if int(category.id) in designers_branch_ids:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ветка «Дизайнеры» синхронизируется автоматически и не удаляется вручную")
        if self.category_repo.get_children(category_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Нельзя удалить категорию с дочерними категориями")

        category.deleted_at = datetime.now(timezone.utc)
        self.db.commit()
        return {"ok": True}

    def add_category_keyword(self, category_id: int, payload: CategoryKeywordRequest) -> dict:
        _, _, designers_branch_ids = self._prepare_categories(sync_designers=False)
        category = self.category_repo.get_by_id(category_id)
        if not category or category.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Категория не найдена")
        has_children = len(self.category_repo.get_children(category_id)) > 0
        self._assert_keywords_editable(
            category=category,
            has_children=has_children,
            in_designers_branch=int(category.id) in designers_branch_ids,
        )

        scope = "title" if payload.scope == "title" else "local"
        keyword = normalize_keyword(payload.keyword)
        existing = self.keyword_repo.get_exact(category_id, keyword, scope=scope)
        if existing:
            return {"ok": True, "keyword": keyword, "scope": scope, "duplicated": True}
        self.keyword_repo.create(category_id=category_id, keyword=keyword, keyword_scope=scope)
        self.db.commit()
        return {"ok": True, "keyword": keyword, "scope": scope}

    def remove_category_keyword(self, category_id: int, keyword: str, scope: str | None = None) -> dict:
        _, _, designers_branch_ids = self._prepare_categories(sync_designers=False)
        category = self.category_repo.get_by_id(category_id)
        if not category or category.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Категория не найдена")
        has_children = len(self.category_repo.get_children(category_id)) > 0
        self._assert_keywords_editable(
            category=category,
            has_children=has_children,
            in_designers_branch=int(category.id) in designers_branch_ids,
        )

        normalized = keyword.strip().lower()
        if scope in {"local", "title"}:
            entity = self.keyword_repo.get_exact(category_id, normalized, scope=scope)
        else:
            entity = self.keyword_repo.get_exact(category_id, normalized, scope="local")
            if entity is None:
                entity = self.keyword_repo.get_exact(category_id, normalized, scope="title")
        if not entity:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ключевое слово не найдено")
        self.db.delete(entity)
        self.db.commit()
        return {"ok": True}

    def _get_keyword_enabled_category(self, category_id: int) -> ParserCategory:
        _, _, designers_branch_ids = self._prepare_categories(sync_designers=False)
        category = self.category_repo.get_by_id(category_id)
        if not category or category.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Категория не найдена")
        has_children = len(self.category_repo.get_children(category_id)) > 0
        self._assert_keywords_editable(
            category=category,
            has_children=has_children,
            in_designers_branch=int(category.id) in designers_branch_ids,
        )
        return category

    def list_manual_products(self, category_id: int) -> list[CategoryManualProductResponse]:
        self._get_keyword_enabled_category(category_id)
        rows = self.manual_product_repo.get_by_category(category_id)
        if not rows:
            return []
        product_ids = {int(row.product_id) for row in rows}
        products = (
            self.db.query(
                ParserProduct.id.label("id"),
                ParserProduct.source_id.label("source_id"),
                ParserProduct.title.label("title"),
                ParserProduct.url.label("url"),
                ParserProduct.status.label("status"),
                ParserProduct.image_urls.label("image_urls"),
                ParserProduct.product_type.label("product_type"),
                ParserSource.name.label("source_name"),
            )
            .join(ParserSource, ParserSource.id == ParserProduct.source_id)
            .filter(ParserProduct.id.in_(list(product_ids)))
            .filter(ParserProduct.deleted_at.is_(None))
            .all()
        )
        by_product_id = {int(item.id): item for item in products}
        response: list[CategoryManualProductResponse] = []
        for row in rows:
            item = by_product_id.get(int(row.product_id))
            if not item:
                continue
            image_url = None
            if isinstance(item.image_urls, list) and item.image_urls:
                image_url = str(item.image_urls[0] or "").strip() or None
            category_name = str(item.product_type or "").strip()
            response.append(
                CategoryManualProductResponse(
                    product_id=int(item.id),
                    source_id=int(item.source_id),
                    source_name=str(item.source_name) if item.source_name else None,
                    title=str(item.title),
                    url=str(item.url),
                    status=str(item.status),
                    image_url=image_url,
                    category_names=[category_name] if category_name else [],
                )
            )
        return response

    def search_manual_products(self, category_id: int, query: str, limit: int = 3) -> list[CategoryManualProductResponse]:
        self._get_keyword_enabled_category(category_id)
        normalized = (query or "").strip()
        if not normalized:
            return []
        safe_limit = max(1, min(int(limit or 3), 20))
        scan_limit = max(40, min(200, safe_limit * 20))
        existing = {int(item.product_id) for item in self.manual_product_repo.get_by_category(category_id)}
        pattern = f"%{normalized}%"
        rows = (
            self.db.query(
                ParserProduct.id.label("id"),
                ParserProduct.source_id.label("source_id"),
                ParserProduct.title.label("title"),
                ParserProduct.url.label("url"),
                ParserProduct.status.label("status"),
                ParserProduct.image_urls.label("image_urls"),
                ParserProduct.product_type.label("product_type"),
                ParserSource.name.label("source_name"),
            )
            .join(ParserSource, ParserSource.id == ParserProduct.source_id)
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProduct.status == "available")
            .filter(
                or_(
                    ParserProduct.title.ilike(pattern),
                    ParserProduct.vendor.ilike(pattern),
                    ParserProduct.product_type.ilike(pattern),
                    ParserProduct.handle.ilike(pattern),
                    ParserProduct.url.ilike(pattern),
                    ParserSource.name.ilike(pattern),
                )
            )
            .order_by(ParserProduct.updated_at.desc(), ParserProduct.id.desc())
            .limit(scan_limit)
            .all()
        )
        candidates: list = []
        for row in rows:
            if int(row.id) in existing:
                continue
            candidates.append(row)
            if len(candidates) >= safe_limit:
                break
        if not candidates:
            return []
        response: list[CategoryManualProductResponse] = []
        for item in candidates:
            image_url = None
            if isinstance(item.image_urls, list) and item.image_urls:
                image_url = str(item.image_urls[0] or "").strip() or None
            category_name = str(item.product_type or "").strip()
            response.append(
                CategoryManualProductResponse(
                    product_id=int(item.id),
                    source_id=int(item.source_id),
                    source_name=str(item.source_name) if item.source_name else None,
                    title=str(item.title),
                    url=str(item.url),
                    status=str(item.status),
                    image_url=image_url,
                    category_names=[category_name] if category_name else [],
                )
            )
        return response

    def add_manual_product(self, category_id: int, product_id: int) -> dict:
        self._get_keyword_enabled_category(category_id)
        product = self.product_repo.get_active_by_id(product_id)
        if product is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")
        existing = self.manual_product_repo.get_exact(category_id, product_id)
        if existing is not None:
            return {"ok": True, "duplicated": True}
        self.manual_product_repo.create(category_id=category_id, product_id=product_id)
        self.db.commit()
        return {"ok": True}

    def remove_manual_product(self, category_id: int, product_id: int) -> dict:
        self._get_keyword_enabled_category(category_id)
        existing = self.manual_product_repo.get_exact(category_id, product_id)
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден в ручных назначениях")
        self.db.delete(existing)
        self.db.commit()
        return {"ok": True}
