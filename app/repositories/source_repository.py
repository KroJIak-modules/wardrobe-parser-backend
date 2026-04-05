"""Repository for parser sources."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ParserSource
from app.repositories.base import BaseRepository


class ParserSourceRepository(BaseRepository[ParserSource]):
    def __init__(self, session: Session):
        super().__init__(session, ParserSource)

    def count_by_supplier_id(self, supplier_id: int) -> int:
        return (
            self.query()
            .filter(ParserSource.supplier_id == supplier_id)
            .filter(ParserSource.deleted_at.is_(None))
            .count()
        )
