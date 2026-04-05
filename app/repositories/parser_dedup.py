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
