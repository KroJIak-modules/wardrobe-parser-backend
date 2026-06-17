"""Data access layer repositories."""

from app.repositories.base import BaseRepository
from app.repositories.admin_auth import AdminRoleRepository, AdminUserRepository
from app.repositories.catalog_dedup import CatalogDedupRepository
from app.repositories.catalog_products import CatalogProductRepository
from app.repositories.catalog_settings import CatalogPricingSettingsRepository, CatalogSupplierRepository, CatalogWeightRuleRepository
from app.repositories.catalog_sources import CatalogSourceRepository
from app.repositories.catalog_sync import CatalogSyncRepository
from app.repositories.catalog_taxonomy import CatalogTaxonomyRepository

__all__ = [
    "BaseRepository",
    "AdminRoleRepository",
    "AdminUserRepository",
    "CatalogDedupRepository",
    "CatalogPricingSettingsRepository",
    "CatalogProductRepository",
    "CatalogSupplierRepository",
    "CatalogSourceRepository",
    "CatalogSyncRepository",
    "CatalogTaxonomyRepository",
    "CatalogWeightRuleRepository",
]
