"""Repository for parser sources."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session

from app.models import ParserSource
from app.repositories.base import BaseRepository


class ParserSourceRepository(BaseRepository[ParserSource]):
    def __init__(self, session: Session):
        super().__init__(session, ParserSource)

    def get_active_by_id(self, source_id: int) -> ParserSource | None:
        return (
            self.query()
            .filter(ParserSource.id == source_id)
            .filter(ParserSource.deleted_at.is_(None))
            .first()
        )

    def get_active_by_ids(self, source_ids: Iterable[int]) -> list[ParserSource]:
        ids = [int(source_id) for source_id in source_ids]
        if not ids:
            return []
        return (
            self.query()
            .filter(ParserSource.id.in_(ids))
            .filter(ParserSource.deleted_at.is_(None))
            .all()
        )

    def count_by_supplier_id(self, supplier_id: int) -> int:
        return (
            self.query()
            .filter(ParserSource.supplier_id == supplier_id)
            .filter(ParserSource.deleted_at.is_(None))
            .count()
        )
