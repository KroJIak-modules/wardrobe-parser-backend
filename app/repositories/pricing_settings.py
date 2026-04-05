"""Repository for pricing settings (formula parameters)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ParserPricingSettings
from app.repositories.base import BaseRepository


class ParserPricingSettingsRepository(BaseRepository[ParserPricingSettings]):
    def __init__(self, session: Session):
        super().__init__(session, ParserPricingSettings)

    def get_singleton(self) -> ParserPricingSettings | None:
        return self.query().order_by(ParserPricingSettings.id.asc()).first()

    def get_or_create_default(self) -> tuple[ParserPricingSettings, bool]:
        current = self.get_singleton()
        if current:
            return current, False
        created = self.create()
        self.flush()
        return created, True
