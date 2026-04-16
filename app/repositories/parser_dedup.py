"""Repository for dedup candidate moderation decisions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ParserDedupDecision
from app.repositories.base import BaseRepository


class ParserDedupDecisionRepository(BaseRepository[ParserDedupDecision]):
    def __init__(self, session: Session):
        super().__init__(session, ParserDedupDecision)

    def get_by_pair_key(self, pair_key: str) -> ParserDedupDecision | None:
        return self.query().filter(ParserDedupDecision.pair_key == pair_key).first()

    def list_pair_keys(self) -> set[str]:
        rows = self.query().with_entities(ParserDedupDecision.pair_key).all()
        return {str(item[0]) for item in rows if item and item[0]}

    def list_recent(self, *, limit: int = 200) -> list[ParserDedupDecision]:
        safe_limit = max(1, min(int(limit), 1000))
        return (
            self.query()
            .order_by(ParserDedupDecision.decided_at.desc(), ParserDedupDecision.id.desc())
            .limit(safe_limit)
            .all()
        )
