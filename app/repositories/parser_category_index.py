"""Repositories for resolved category index and count snapshots."""

from __future__ import annotations

from sqlalchemy import case
from sqlalchemy.orm import Session

from app.models import ParserCategoryCountSnapshot, ParserCategoryIndexState, ParserProductCategoryMatch
from app.repositories.base import BaseRepository


class ParserProductCategoryMatchRepository(BaseRepository[ParserProductCategoryMatch]):
    def __init__(self, session: Session):
        super().__init__(session, ParserProductCategoryMatch)

    def get_grouped_category_ids(self, product_ids: set[int]) -> dict[int, list[int]]:
        if not product_ids:
            return {}
        rows = (
            self.query()
            .filter(ParserProductCategoryMatch.product_id.in_(list(product_ids)))
            .order_by(
                ParserProductCategoryMatch.product_id.asc(),
                case((ParserProductCategoryMatch.match_source == "manual", 0), else_=1).asc(),
                ParserProductCategoryMatch.score.desc(),
                ParserProductCategoryMatch.category_id.asc(),
            )
            .all()
        )
        grouped: dict[int, list[int]] = {}
        for row in rows:
            product_id = int(row.product_id)
            category_id = int(row.category_id)
            bucket = grouped.setdefault(product_id, [])
            if category_id in bucket:
                continue
            bucket.append(category_id)
        return grouped


class ParserCategoryCountSnapshotRepository(BaseRepository[ParserCategoryCountSnapshot]):
    def __init__(self, session: Session):
        super().__init__(session, ParserCategoryCountSnapshot)

    def get_subtree_count_map(self) -> dict[int, int]:
        rows = self.query().all()
        return {int(row.category_id): int(row.subtree_count or 0) for row in rows}


class ParserCategoryIndexStateRepository(BaseRepository[ParserCategoryIndexState]):
    def __init__(self, session: Session):
        super().__init__(session, ParserCategoryIndexState)

    def get_singleton(self) -> ParserCategoryIndexState | None:
        return self.get_by_id(1)

    def get_or_create_singleton(self) -> ParserCategoryIndexState:
        current = self.get_singleton()
        if current is not None:
            return current
        created = self.create(id=1)
        self.flush()
        return created
