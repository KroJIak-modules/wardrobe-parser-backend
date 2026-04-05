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
            .order_by(ParserCategory.parent_id.asc().nullsfirst(), ParserCategory.name.asc())
            .all()
        )

    def get_fallback(self) -> ParserCategory | None:
        return (
            self.query()
            .filter(ParserCategory.deleted_at.is_(None))
            .filter(ParserCategory.is_fallback.is_(True))
            .first()
        )

    def get_by_slug(self, slug: str) -> ParserCategory | None:
        return (
            self.query()
            .filter(ParserCategory.deleted_at.is_(None))
            .filter(ParserCategory.slug == slug)
            .first()
        )

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

    def get_by_category(self, category_id: int) -> list[ParserCategoryKeyword]:
        return (
            self.query()
            .filter(ParserCategoryKeyword.category_id == category_id)
            .order_by(ParserCategoryKeyword.keyword.asc())
            .all()
        )

    def get_exact(self, category_id: int, keyword: str) -> ParserCategoryKeyword | None:
        return (
            self.query()
            .filter(ParserCategoryKeyword.category_id == category_id)
            .filter(ParserCategoryKeyword.keyword == keyword)
            .first()
        )
