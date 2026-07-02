from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

from app.schemas.admin_settings import (
    SettingsTransferAdminUiSettings,
    SettingsTransferPricingSettings,
    SettingsTransferSupplierEntry,
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


class DefaultAdminSettingsLoader:
    _ENV_PATH_KEY = "ADMIN_DEFAULTS_CONFIG_PATH"
    _LOCAL_SHARED_FILE_PATH = Path(__file__).resolve().parents[4] / "service" / "config" / "sources.json"
    _CONTAINER_SHARED_FILE_PATH = Path(__file__).resolve().parents[3] / "shared-config" / "sources.json"

    @classmethod
    def _resolve_file_path(cls) -> Path:
        explicit_path = str(os.getenv(cls._ENV_PATH_KEY, "") or "").strip()
        if explicit_path:
            return Path(explicit_path)
        if cls._CONTAINER_SHARED_FILE_PATH.exists():
            return cls._CONTAINER_SHARED_FILE_PATH
        return cls._LOCAL_SHARED_FILE_PATH

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls) -> DefaultAdminSettingsSeed:
        payload = json.loads(cls._resolve_file_path().read_text(encoding="utf-8"))
        defaults = payload.get("admin_defaults") if isinstance(payload, dict) else None
        if not isinstance(defaults, dict):
            raise ValueError("admin_defaults section is missing in shared sources config")
        return DefaultAdminSettingsSeed.model_validate(defaults)
