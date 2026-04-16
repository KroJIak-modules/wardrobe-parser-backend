"""Data access layer repositories."""

from app.repositories.base import BaseRepository
from app.repositories.parser_category import ParserCategoryKeywordRepository, ParserCategoryRepository
from app.repositories.parser_category_index import (
    ParserCategoryCountSnapshotRepository,
    ParserCategoryIndexStateRepository,
    ParserProductCategoryMatchRepository,
)
from app.repositories.parser_category_manual_product import ParserCategoryManualProductRepository
from app.repositories.parser_dedup import ParserDedupDecisionRepository
from app.repositories.parser_favorite_product import ParserFavoriteProductRepository
from app.repositories.parser_product import ParserProductRepository
from app.repositories.pricing_settings import ParserPricingSettingsRepository
from app.repositories.pricing_suppliers import ParserSupplierRepository
from app.repositories.source_repository import ParserSourceRepository
from app.repositories.weight_settings import ParserWeightKeywordRepository, ParserWeightRuleRepository

__all__ = [
    "BaseRepository",
    "ParserCategoryKeywordRepository",
    "ParserCategoryCountSnapshotRepository",
    "ParserCategoryIndexStateRepository",
    "ParserCategoryManualProductRepository",
    "ParserCategoryRepository",
    "ParserProductCategoryMatchRepository",
    "ParserDedupDecisionRepository",
    "ParserFavoriteProductRepository",
    "ParserPricingSettingsRepository",
    "ParserProductRepository",
    "ParserSourceRepository",
    "ParserSupplierRepository",
    "ParserWeightKeywordRepository",
    "ParserWeightRuleRepository",
]
