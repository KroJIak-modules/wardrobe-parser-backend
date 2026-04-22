"""Repository for parser brand mappings."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ParserBrandMapping
from app.repositories.base import BaseRepository


class ParserBrandMappingRepository(BaseRepository[ParserBrandMapping]):
    def __init__(self, session: Session):
        super().__init__(session, ParserBrandMapping)

    def list_all(self) -> list[ParserBrandMapping]:
        return self.query().order_by(ParserBrandMapping.source_brand_key.asc()).all()

    def delete_all(self) -> None:
        self.query().delete(synchronize_session=False)

    def create_mapping(
        self,
        *,
        source_brand: str,
        source_brand_key: str,
        target_brand: str,
        include_in_designers: bool,
    ) -> ParserBrandMapping:
        entity = ParserBrandMapping(
            source_brand=source_brand,
            source_brand_key=source_brand_key,
            target_brand=target_brand,
            include_in_designers=bool(include_in_designers),
        )
        self.session.add(entity)
        return entity
