"""Repositories for category tree and keyword rules."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ParserCategory, ParserCategoryKeyword
from app.repositories.base import BaseRepository


class ParserCategoryRepository(BaseRepository[ParserCategory]):
    def __init__(self, session: Session):
        super().__init__(session, ParserCategory)

    def get_all_active(self) -> list[ParserCategory]:
        return (
            self.query()
            .filter(ParserCategory.deleted_at.is_(None))
            # Keep explicit UI/menu order stable by creation order inside each parent.
            .order_by(ParserCategory.parent_id.asc().nullsfirst(), ParserCategory.id.asc())
            .all()
        )

    def get_fallback(self) -> ParserCategory | None:
        return (
            self.query()
            .filter(ParserCategory.deleted_at.is_(None))
            .filter(ParserCategory.is_fallback.is_(True))
            .first()
        )

    def get_favorite(self) -> ParserCategory | None:
        return (
            self.query()
            .filter(ParserCategory.deleted_at.is_(None))
            .filter(ParserCategory.is_favorite.is_(True))
            .first()
        )

    def get_favorites(self) -> list[ParserCategory]:
        return (
            self.query()
            .filter(ParserCategory.deleted_at.is_(None))
            .filter(ParserCategory.is_favorite.is_(True))
            .order_by(ParserCategory.id.asc())
            .all()
        )

    def get_by_slug(self, slug: str) -> ParserCategory | None:
        return (
            self.query()
            .filter(ParserCategory.deleted_at.is_(None))
            .filter(ParserCategory.slug == slug)
            .first()
        )

    def get_by_slug_any(self, slug: str) -> ParserCategory | None:
        return self.query().filter(ParserCategory.slug == slug).first()

    def get_children(self, parent_id: int) -> list[ParserCategory]:
        return (
            self.query()
            .filter(ParserCategory.deleted_at.is_(None))
            .filter(ParserCategory.parent_id == parent_id)
            .all()
        )


class ParserCategoryKeywordRepository(BaseRepository[ParserCategoryKeyword]):
    def __init__(self, session: Session):
        super().__init__(session, ParserCategoryKeyword)

    @staticmethod
    def _normalize_scope(scope: str | None) -> str:
        return "title" if (scope or "").strip().lower() == "title" else "local"

    def get_by_category(self, category_id: int, scope: str | None = None) -> list[ParserCategoryKeyword]:
        normalized_scope = self._normalize_scope(scope) if scope is not None else None
        query = self.query().filter(ParserCategoryKeyword.category_id == category_id)
        if normalized_scope is not None:
            query = query.filter(ParserCategoryKeyword.keyword_scope == normalized_scope)
        return query.order_by(ParserCategoryKeyword.keyword.asc()).all()

    def get_exact(self, category_id: int, keyword: str, scope: str | None = "local") -> ParserCategoryKeyword | None:
        normalized_scope = self._normalize_scope(scope)
        return (
            self.query()
            .filter(ParserCategoryKeyword.category_id == category_id)
            .filter(ParserCategoryKeyword.keyword == keyword)
            .filter(ParserCategoryKeyword.keyword_scope == normalized_scope)
            .first()
        )

    def get_grouped_keywords(self, scope: str | None = "local") -> dict[int, list[str]]:
        normalized_scope = self._normalize_scope(scope)
        grouped: dict[int, list[str]] = {}
        rows = (
            self.query()
            .filter(ParserCategoryKeyword.keyword_scope == normalized_scope)
            .order_by(ParserCategoryKeyword.category_id.asc(), ParserCategoryKeyword.keyword.asc())
            .all()
        )
        for row in rows:
            grouped.setdefault(int(row.category_id), []).append(str(row.keyword))
        return grouped
