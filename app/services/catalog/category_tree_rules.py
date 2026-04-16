"""Validation and mutation helpers for category tree service."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import ParserCategory
from app.repositories import ParserCategoryKeywordRepository, ParserCategoryRepository
from app.services.catalog.category_tree_utils import is_descendant


_CYRILLIC_TO_LATIN = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def _transliterate(value: str) -> str:
    chunks: list[str] = []
    for char in value.lower():
        chunks.append(_CYRILLIC_TO_LATIN.get(char, char))
    return "".join(chunks)


def slugify(value: str) -> str:
    latin = _transliterate(value)
    slug = re.sub(r"[^a-z0-9]+", "-", latin).strip("-")
    return slug or "category"


def build_unique_slug(*, name: str, category_repo: ParserCategoryRepository, exclude_category_id: int | None = None) -> str:
    slug = slugify(name)
    maybe = category_repo.get_by_slug_any(slug)
    if maybe and maybe.id != exclude_category_id:
        slug = f"{slug}-{int(datetime.now(timezone.utc).timestamp())}"
    return slug


def ensure_fallback(category_repo: ParserCategoryRepository) -> ParserCategory:
    fallback = category_repo.get_fallback()
    if fallback:
        return fallback
    created = category_repo.create(name="Прочее", slug="prochee", parent_id=None, is_fallback=True, is_enabled=True)
    category_repo.flush()
    return created


def purge_fallback_keywords(*, db: Session, keyword_repo: ParserCategoryKeywordRepository, fallback: ParserCategory) -> None:
    stale = keyword_repo.get_by_category(fallback.id)
    if not stale:
        return
    for item in stale:
        db.delete(item)


def validate_parent_for_create(*, category_repo: ParserCategoryRepository, parent_id: int | None) -> None:
    if parent_id is None:
        return
    parent = category_repo.get_by_id(parent_id)
    if not parent or parent.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Родительская категория не найдена")


def validate_parent_for_update(*, category: ParserCategory, parent_id: int | None, category_repo: ParserCategoryRepository) -> None:
    if parent_id is None:
        category.parent_id = None
        return
    if parent_id == category.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Категория не может быть своим родителем")
    parent = category_repo.get_by_id(parent_id)
    if not parent or parent.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Новый родитель не найден")
    if is_descendant(category_repo.get_all_active(), category.id, parent_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя переместить категорию внутрь своего потомка",
        )
    category.parent_id = parent_id


def normalize_keyword(keyword: str) -> str:
    normalized = keyword.strip().lower()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ключевое слово не может быть пустым")
    return normalized
