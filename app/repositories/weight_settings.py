"""Repositories for weight rules and their keywords."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ParserWeightKeyword, ParserWeightRule
from app.repositories.base import BaseRepository


class ParserWeightRuleRepository(BaseRepository[ParserWeightRule]):
    def __init__(self, session: Session):
        super().__init__(session, ParserWeightRule)

    def get_all_active(self) -> list[ParserWeightRule]:
        return (
            self.query()
            .filter(ParserWeightRule.deleted_at.is_(None))
            .order_by(ParserWeightRule.sort_order.asc(), ParserWeightRule.weight_grams.asc(), ParserWeightRule.id.asc())
            .all()
        )


class ParserWeightKeywordRepository(BaseRepository[ParserWeightKeyword]):
    def __init__(self, session: Session):
        super().__init__(session, ParserWeightKeyword)

    def get_by_rule(self, rule_id: int) -> list[ParserWeightKeyword]:
        return (
            self.query()
            .filter(ParserWeightKeyword.rule_id == rule_id)
            .order_by(ParserWeightKeyword.keyword.asc())
            .all()
        )

    def get_exact(self, rule_id: int, keyword: str) -> ParserWeightKeyword | None:
        return (
            self.query()
            .filter(ParserWeightKeyword.rule_id == rule_id)
            .filter(ParserWeightKeyword.keyword == keyword)
            .first()
        )
