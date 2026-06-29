from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path

from pydantic import BaseModel, Field

from app.schemas.admin_settings import (
    SettingsTransferAdminUiSettings,
    SettingsTransferPricingSettings,
    SettingsTransferSupplierEntry,
    SettingsTransferWeightRuleEntry,
)


class DefaultSourceSettingSeed(BaseModel):
    enabled: bool = True
    sync_enabled: bool = True
    dedup_enabled: bool = True
    hide_auto_added_products: bool = False
    description_mode: str = "text"
    show_images: bool = True
    promo_factor: float = Field(default=1.0, ge=0.0, le=10.0)
    promo_only_no_discount: bool = False
    buyout_surcharge_value: float | None = Field(default=None, ge=0.0, le=100000000.0)
    buyout_surcharge_currency: str | None = Field(default=None, min_length=3, max_length=3)


class DefaultAdminSettingsSeed(BaseModel):
    pricing_settings: SettingsTransferPricingSettings
    admin_ui_settings: SettingsTransferAdminUiSettings
    default_source_supplier_key: str = Field(min_length=1, max_length=64)
    source_setting_defaults: DefaultSourceSettingSeed
    suppliers: list[SettingsTransferSupplierEntry] = Field(default_factory=list)
    weight_rules: list[SettingsTransferWeightRuleEntry] = Field(default_factory=list)


class DefaultAdminSettingsLoader:
    _FILE_PATH = Path(__file__).resolve().parents[3] / "config" / "default-admin-settings.json"

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls) -> DefaultAdminSettingsSeed:
        payload = json.loads(cls._FILE_PATH.read_text(encoding="utf-8"))
        return DefaultAdminSettingsSeed.model_validate(payload)
