"""Export/import of admin settings (without product data)."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

import requests
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    AdminUiSettings,
    ParserBrandMapping,
    PricingSetting,
    Source,
    Supplier,
    SupplierShippingRate,
    WeightRule,
    WeightRuleKeyword,
)
from app.repositories import (
    CatalogPricingSettingsRepository,
    CatalogSourceRepository,
    CatalogSupplierRepository,
    CatalogWeightRuleRepository,
)
from app.services.catalog.source_registry_service import SourceRegistryService
from app.services.settings.pricing_service import PricingSettingsService
from app.schemas.parser import (
    SettingsTransferBrandMappingEntry,
    SettingsTransferAdminUiSettings,
    SettingsTransferPayload,
    SettingsTransferPricingSettings,
    SettingsTransferResponse,
    SettingsTransferSourceEntry,
    SettingsTransferSupplierEntry,
    SettingsTransferWeightRuleEntry,
)

_SCHEMA_VERSION = 1
_PROJECT_NAME = "wardrobe-parser-platform"

_PRICING_EXPORT_FIELDS = [
    "markup_multiplier",
    "weight_tolerance",
    "promo_factor",
    "customs_threshold_eur",
    "customs_threshold_currency",
    "customs_duty_rate",
    "bybit_extra_rub",
    "eur_to_usd_rate",
    "gbp_to_usd_rate",
    "jpy_to_usd_rate",
    "final_rounding_mode",
    "payment_fee_rate",
    "customs_processing_rate",
    "customs_fixed_rub",
    "shipping_alt_threshold_eur",
    "tax_rate",
    "dedup_only_available_products",
    "show_product_description",
    "svc_rules",
    "insurance_rules",
    "service_fee_rules",
]

_PRICING_IMPORT_FIELDS = set(_PRICING_EXPORT_FIELDS)
_DEFAULT_CURRENCY_PRIORITY = ["USD", "EUR", "GBP"]


def _normalize_currency(raw: str | None, *, default: str = "RUB") -> str:
    value = (raw or default).strip().upper()
    if value not in {"RUB", "USD", "EUR", "GBP", "JPY"}:
        return default
    return value


def _normalize_supplier_key(raw_key: str, fallback_name: str, index: int) -> str:
    source = (raw_key or "").strip().lower()
    if not source:
        source = fallback_name.strip().lower()
    source = re.sub(r"[^a-z0-9]+", "-", source).strip("-")
    if not source:
        source = f"supplier-{index}"
    return source[:64]


def _norm_host(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return ""
    value = re.sub(r"^https?://", "", value)
    return value.split("/", 1)[0]


class SettingsTransferService:
    """Application service for settings export/import."""

    def __init__(self, db: Session):
        self.db = db
        self.pricing_repo = CatalogPricingSettingsRepository(db)
        self.supplier_repo = CatalogSupplierRepository(db)
        self.source_repo = CatalogSourceRepository(db)
        self.weight_rule_repo = CatalogWeightRuleRepository(db)

    @staticmethod
    def _service_sources_base() -> str:
        return f"{settings.service_base_url.rstrip('/')}/api/v1/sync/sources"

    def _service_list(self) -> list[dict[str, Any]]:
        try:
            res = requests.get(self._service_sources_base(), timeout=(5, 30))
            res.raise_for_status()
            payload = res.json()
        except requests.RequestException as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Service API unavailable: {exc}") from exc
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _service_patch(self, source_key: str, payload: dict[str, Any]) -> None:
        try:
            res = requests.patch(f"{self._service_sources_base()}/{source_key}", json=payload, timeout=(5, 30))
            res.raise_for_status()
        except requests.RequestException as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Service API unavailable: {exc}") from exc

    @staticmethod
    def _supplier_alt_position(supplier: Supplier) -> int:
        parent = getattr(supplier, "parent_supplier", None)
        if parent is None:
            return 0
        ordered_children = sorted(
            [child for child in getattr(parent, "children", []) if child is not None],
            key=lambda child: int(getattr(child, "id", 0)),
        )
        for index, child in enumerate(ordered_children, start=1):
            if int(getattr(child, "id", 0)) == int(supplier.id):
                return index
        return 0

    def export_payload(self) -> SettingsTransferPayload:
        pricing_row, _ = self.pricing_repo.get_or_create_default()
        PricingSettingsService._sync_legacy_pricing_aliases(pricing_row)
        suppliers = self.supplier_repo.list_all_with_rates()
        sources = self.source_repo.list_all()
        weight_rules = self.weight_rule_repo.list_active()

        supplier_by_id = {int(supplier.id): supplier for supplier in suppliers}

        pricing = SettingsTransferPricingSettings(
            **{
                field: getattr(pricing_row, field)
                for field in _PRICING_EXPORT_FIELDS
            }
        )
        ui_row = self.db.query(AdminUiSettings).filter(AdminUiSettings.id == 1).one_or_none()
        admin_ui = SettingsTransferAdminUiSettings(
            designers_min_products=max(1, int(getattr(ui_row, "designers_min_products", 1) or 1)),
            designers_exclude_store_vendors=bool(getattr(ui_row, "designers_exclude_store_vendors", False)),
            auto_sync_period_minutes=max(60, int(getattr(ui_row, "auto_sync_period_minutes", 60) or 60)),
            showcase_hero_image_asset_id=(
                int(getattr(ui_row, "showcase_hero_image_asset_id"))
                if isinstance(getattr(ui_row, "showcase_hero_image_asset_id", None), int)
                and int(getattr(ui_row, "showcase_hero_image_asset_id")) > 0
                else None
            ),
            showcase_carousel_image_asset_ids=PricingSettingsService._normalize_image_asset_ids(
                getattr(ui_row, "showcase_carousel_image_asset_ids", None),
                limit=20,
            ),
        )

        supplier_entries = [
            SettingsTransferSupplierEntry(
                key=str(supplier.key),
                name=str(supplier.name),
                category=str(getattr(supplier, "provider_kind", "main") or "main"),
                parent_supplier_key=(
                    str(supplier_by_id[int(supplier.parent_supplier_id)].key)
                    if getattr(supplier, "parent_supplier_id", None) is not None
                    and int(supplier.parent_supplier_id) in supplier_by_id
                    else None
                ),
                alt_position=self._supplier_alt_position(supplier),
                rate_currency=str(supplier.rate_currency),
                rates=[
                    {
                        "min_kg": float(rate.min_weight_kg),
                        "max_kg": (float(rate.max_weight_kg) if rate.max_weight_kg is not None else None),
                        "rub": float(rate.price_rub),
                    }
                    for rate in sorted(
                        supplier.shipping_rates,
                        key=lambda item: (
                            float(item.min_weight_kg or 0.0),
                            float(item.max_weight_kg) if item.max_weight_kg is not None else float("inf"),
                        ),
                    )
                ],
            )
            for supplier in suppliers
        ]

        service_sources = self._service_list()
        service_by_host: dict[str, dict[str, Any]] = {}
        for item in service_sources:
            host = _norm_host(item.get("url"))
            if host and host not in service_by_host:
                service_by_host[host] = item

        source_entries: list[SettingsTransferSourceEntry] = []
        for source in sources:
            setting = self.source_repo.ensure_setting(source)
            service_item = service_by_host.get(_norm_host(source.base_url))
            cfg = service_item.get("config") if isinstance(service_item, dict) and isinstance(service_item.get("config"), dict) else {}
            currency_cfg = cfg.get("shopify_currency") if isinstance(cfg.get("shopify_currency"), dict) else {}
            currency_priority_raw = currency_cfg.get("requested_currency_priority")
            currency_priority = [
                str(x).strip().upper()
                for x in (currency_priority_raw if isinstance(currency_priority_raw, list) else _DEFAULT_CURRENCY_PRIORITY)
                if str(x).strip()
            ] or list(_DEFAULT_CURRENCY_PRIORITY)
            currency_method = str(currency_cfg.get("method") or "priority_list").strip().lower()
            if currency_method not in {"priority_list", "locked_param_currency", "locked_no_currency"}:
                currency_method = "priority_list"
            locked_currency = str(currency_cfg.get("locked_currency") or "").strip().upper() or None
            if locked_currency == "GBR":
                locked_currency = "GBP"
            if locked_currency not in {"USD", "EUR", "GBP", "JPY"}:
                locked_currency = None

            source_entries.append(
                SettingsTransferSourceEntry(
                    name=str(source.name),
                    url=str(source.base_url),
                    enabled=bool(getattr(setting, "is_enabled", True)),
                    sync_enabled=bool(getattr(setting, "is_sync_enabled", True)),
                    hide_auto_added_products=bool(getattr(setting, "hide_auto_added_products", False)),
                    show_description=str(getattr(setting, "description_mode", "text") or "text") != "hidden",
                    show_images=bool(getattr(setting, "show_images", True)),
                    currency_priority=currency_priority,
                    currency_method=currency_method,  # type: ignore[arg-type]
                    locked_currency=locked_currency,
                    supplier_key=(
                        str(supplier_by_id[int(setting.supplier_id)].key)
                        if getattr(setting, "supplier_id", None) is not None and int(setting.supplier_id) in supplier_by_id
                        else None
                    ),
                    promo_factor=float(getattr(setting, "promo_factor", 1.0) or 1.0),
                    promo_only_no_discount=bool(getattr(setting, "promo_only_no_discount", False)),
                    buyout_surcharge_value=float(getattr(setting, "buyout_surcharge_value", 0.0) or 0.0),
                    buyout_surcharge_currency=_normalize_currency(getattr(setting, "buyout_surcharge_currency", None), default="RUB"),
                )
            )

        weight_entries = [
            SettingsTransferWeightRuleEntry(
                weight_grams=int(rule.weight_grams),
                sort_order=index,
                keywords=[
                    str(item.keyword)
                    for item in self.weight_rule_repo.list_keywords(int(rule.id))
                ],
            )
            for index, rule in enumerate(weight_rules)
        ]

        return SettingsTransferPayload(
            schema_version=_SCHEMA_VERSION,
            exported_at=datetime.now(timezone.utc).isoformat(),
            project=_PROJECT_NAME,
            pricing_settings=pricing,
            admin_ui_settings=admin_ui,
            suppliers=supplier_entries,
            sources=source_entries,
            weight_rules=weight_entries,
            categories=[],
            category_keywords=[],
            brand_mappings=[
                SettingsTransferBrandMappingEntry(
                    source_brand=str(row.source_brand),
                    source_brand_key=str(row.source_brand_key),
                    target_brand=str(row.target_brand),
                    include_in_designers=bool(getattr(row, "include_in_designers", True)),
                )
                for row in self.db.query(ParserBrandMapping).order_by(ParserBrandMapping.id.asc()).all()
            ],
        )

    def import_payload(self, payload: SettingsTransferPayload) -> SettingsTransferResponse:
        if int(payload.schema_version) != _SCHEMA_VERSION:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported schema_version: {payload.schema_version}",
            )

        supplier_map = self._import_suppliers(payload.suppliers)
        pricing_updated = self._import_pricing(payload.pricing_settings)
        admin_ui_updated = self._import_admin_ui(payload.admin_ui_settings)
        source_count = self._import_sources(payload.sources, supplier_map=supplier_map)
        weight_count = self._import_weight_rules(payload.weight_rules)
        brand_mappings_updated = self._import_brand_mappings(payload.brand_mappings)

        self.db.commit()
        return SettingsTransferResponse(
            ok=True,
            message="Настройки импортированы",
            schema_version=_SCHEMA_VERSION,
            imported_at=datetime.now(timezone.utc).isoformat(),
            imported_counts={
                "pricing_settings_updated": pricing_updated,
                "admin_ui_settings_updated": admin_ui_updated,
                "suppliers_upserted": len(supplier_map),
                "sources_upserted": source_count,
                "weight_rules_replaced": weight_count,
                "categories_upserted": 0,
                "category_keywords_replaced": 0,
                "brand_mappings_replaced": brand_mappings_updated,
            },
        )

    def reset_all(self) -> SettingsTransferResponse:
        # 1) Reset pricing settings/suppliers to service defaults.
        self.db.query(SupplierShippingRate).delete(synchronize_session=False)
        self.db.query(Supplier).delete(synchronize_session=False)
        self.db.query(PricingSetting).delete(synchronize_session=False)
        self.db.query(AdminUiSettings).delete(synchronize_session=False)
        self.db.flush()

        pricing_service = PricingSettingsService(self.db)
        pricing_service.get_settings(refresh_bybit=False)
        suppliers = self.supplier_repo.list_all_with_rates()
        fallback_supplier = next((s for s in suppliers if str(getattr(s, "provider_kind", "")) == "main"), suppliers[0] if suppliers else None)

        # 2) Reset sources to neutral defaults.
        sources_reset = 0
        for source in self.source_repo.list_all():
            setting = self.source_repo.ensure_setting(source)
            setting.is_enabled = True
            setting.is_sync_enabled = True
            setting.hide_auto_added_products = False
            setting.description_mode = "text"
            setting.show_images = True
            setting.promo_factor = 1.0
            setting.promo_only_no_discount = False
            setting.buyout_surcharge_value = 0.0
            setting.buyout_surcharge_currency = "RUB"
            if fallback_supplier is not None:
                setting.supplier_id = int(fallback_supplier.id)
            sources_reset += 1

        # 3) Reset weight rules.
        self.db.query(WeightRuleKeyword).delete(synchronize_session=False)
        self.db.query(WeightRule).delete(synchronize_session=False)

        # 4) Reset designers remapping.
        self.db.query(ParserBrandMapping).delete(synchronize_session=False)

        self.db.commit()
        return SettingsTransferResponse(
            ok=True,
            message="Настройки сброшены к значениям по умолчанию",
            schema_version=_SCHEMA_VERSION,
            imported_at=datetime.now(timezone.utc).isoformat(),
            imported_counts={
                "pricing_settings_updated": 1,
                "admin_ui_settings_updated": 1,
                "suppliers_upserted": len(suppliers),
                "sources_upserted": sources_reset,
                "weight_rules_replaced": 0,
                "categories_upserted": 0,
                "category_keywords_replaced": 0,
            },
        )

    def _import_pricing(self, payload: SettingsTransferPricingSettings) -> int:
        row, _ = self.pricing_repo.get_or_create_default()
        values = payload.model_dump()
        updated_fields = 0
        mapped_values = {
            "markup_multiplier": float(values["markup_multiplier"]),
            "weight_tolerance": float(values["weight_tolerance"]),
            "customs_threshold_eur": float(values["customs_threshold_eur"]),
            "customs_duty_rate": float(values["customs_duty_rate"]),
            "usdt_extra_rub": float(values["bybit_extra_rub"]),
            "usd_to_rub_rate": float(getattr(row, "usd_to_rub_rate", getattr(row, "usdt_to_rub_rate", 95.0)) or 95.0),
            "usdt_to_rub_rate": float(getattr(row, "usdt_to_rub_rate", 95.0) or 95.0),
            "eur_to_rub_rate": float(values["eur_to_usd_rate"]) * float(
                getattr(row, "usd_to_rub_rate", getattr(row, "usdt_to_rub_rate", 95.0)) or 95.0
            ),
            "final_rounding_mode": str(values["final_rounding_mode"]),
            "payment_fee_rate": float(values["payment_fee_rate"]),
            "customs_processing_rate": float(values["customs_processing_rate"]),
            "customs_fixed_rub": float(values["customs_fixed_rub"]),
            "tax_rate": float(values["tax_rate"]),
        }
        for key, raw_value in mapped_values.items():
            if getattr(row, key) != raw_value:
                setattr(row, key, raw_value)
                updated_fields += 1
        PricingSettingsService._sync_legacy_pricing_aliases(row)
        return updated_fields

    def _import_admin_ui(self, payload: SettingsTransferAdminUiSettings) -> int:
        row = self.db.query(AdminUiSettings).filter(AdminUiSettings.id == 1).one_or_none()
        if row is None:
            row = AdminUiSettings(id=1)
            self.db.add(row)
            self.db.flush()
        updated_fields = 0
        values = payload.model_dump()
        normalized = {
            "designers_min_products": max(1, int(values.get("designers_min_products") or 1)),
            "designers_exclude_store_vendors": bool(values.get("designers_exclude_store_vendors")),
            "auto_sync_period_minutes": max(60, int(values.get("auto_sync_period_minutes") or 60)),
            "showcase_hero_image_asset_id": int(values["showcase_hero_image_asset_id"]) if isinstance(values.get("showcase_hero_image_asset_id"), int) and int(values.get("showcase_hero_image_asset_id")) > 0 else None,
            "showcase_carousel_image_asset_ids": PricingSettingsService._normalize_image_asset_ids(values.get("showcase_carousel_image_asset_ids"), limit=20),
        }
        for key, raw_value in normalized.items():
            if getattr(row, key) != raw_value:
                setattr(row, key, raw_value)
                updated_fields += 1
        return updated_fields

    def _import_suppliers(self, suppliers: list[SettingsTransferSupplierEntry]) -> dict[str, Supplier]:
        existing = {str(item.key): item for item in self.supplier_repo.list_all_with_rates()}
        result: dict[str, Supplier] = {}
        incoming_by_key: dict[str, SettingsTransferSupplierEntry] = {}

        for index, incoming in enumerate(suppliers, start=1):
            key = _normalize_supplier_key(incoming.key, incoming.name, index=index)
            incoming_by_key[key] = incoming
            current = existing.get(key)
            if current is None:
                current = self.supplier_repo.create(
                    key=key,
                    name=incoming.name,
                    provider_kind=incoming.category if incoming.category in {"main", "alt"} else "main",
                    rate_currency=_normalize_currency(incoming.rate_currency, default="RUB"),
                )
                self.db.flush()
            else:
                current.name = incoming.name
                current.provider_kind = incoming.category if incoming.category in {"main", "alt"} else "main"
                current.rate_currency = _normalize_currency(incoming.rate_currency, default="RUB")
            self.supplier_repo.replace_ranges(
                supplier_id=int(current.id),
                ranges=[
                    {
                        "min_kg": max(0.0, float(getattr(rate, "min_kg", 0.0))),
                        "max_kg": getattr(rate, "max_kg", None),
                        "rub": max(0.0, float(getattr(rate, "rub", 0.0))),
                    }
                    for rate in incoming.rates
                ],
            )
            result[key] = current

        # Apply parent/alt linkage after all suppliers exist.
        for key, supplier in result.items():
            incoming = incoming_by_key.get(key)
            if incoming is None:
                continue
            parent_key = _normalize_supplier_key(
                incoming.parent_supplier_key or "",
                incoming.parent_supplier_key or "",
                index=0,
            ) if incoming.parent_supplier_key else None
            parent_supplier = result.get(parent_key) if parent_key else None
            supplier.parent_supplier_id = int(parent_supplier.id) if parent_supplier is not None else None
            supplier.provider_kind = "alt" if supplier.parent_supplier_id is not None else "main"
        return result

    def _import_sources(
        self,
        sources: list[SettingsTransferSourceEntry],
        *,
        supplier_map: dict[str, Supplier],
    ) -> int:
        if not supplier_map:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нет тарифов для назначения источникам")
        updated = 0
        SourceRegistryService(self.db).refresh_from_service()
        service_sources = self._service_list()
        service_by_host: dict[str, dict[str, Any]] = {}
        for item in service_sources:
            host = _norm_host(item.get("url"))
            if host and host not in service_by_host:
                service_by_host[host] = item

        for item in sources:
            source_key = SourceRegistryService.normalize_source_key(item.url)
            existing = self.source_repo.get_by_key(source_key) if source_key else None
            if existing is None:
                existing = self.db.query(Source).filter(Source.base_url == item.url).one_or_none()
            supplier = supplier_map.get(item.supplier_key) if item.supplier_key else None
            if supplier is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Для источника '{item.name}' не найден назначенный тариф",
                )
            supplier_id = int(supplier.id)
            if existing is None:
                existing = self.source_repo.create(
                    key=source_key or SourceRegistryService.normalize_source_key(item.name) or f"source-{updated+1}",
                    name=item.name,
                    base_url=item.url,
                )
            else:
                existing.name = item.name
                existing.base_url = item.url
            setting = self.source_repo.ensure_setting(existing)
            setting.is_enabled = bool(item.enabled)
            setting.is_sync_enabled = bool(item.sync_enabled)
            setting.hide_auto_added_products = bool(item.hide_auto_added_products)
            setting.description_mode = "text" if bool(item.show_description) else "hidden"
            setting.show_images = bool(item.show_images)
            setting.supplier_id = supplier_id
            setting.promo_factor = float(item.promo_factor)
            setting.promo_only_no_discount = bool(item.promo_only_no_discount)
            setting.buyout_surcharge_value = float(item.buyout_surcharge_value)
            setting.buyout_surcharge_currency = _normalize_currency(item.buyout_surcharge_currency, default="RUB")
            updated += 1

            service_item = service_by_host.get(_norm_host(item.url))
            if isinstance(service_item, dict):
                source_key = str(service_item.get("key") or "").strip()
                if source_key:
                    self._service_patch(source_key, {"sync_enabled": bool(item.sync_enabled)})
                    self._service_patch(
                        source_key,
                        {
                            "requested_currency_priority": [
                                str(x).strip().upper()
                                for x in (item.currency_priority or _DEFAULT_CURRENCY_PRIORITY)
                                if str(x).strip()
                            ] or list(_DEFAULT_CURRENCY_PRIORITY),
                            "currency_method": str(item.currency_method),
                            "locked_currency": str(item.locked_currency or "").strip().upper(),
                        },
                    )
        return updated

    def _import_weight_rules(self, rules: list[SettingsTransferWeightRuleEntry]) -> int:
        self.db.query(WeightRuleKeyword).delete(synchronize_session=False)
        self.db.query(WeightRule).delete(synchronize_session=False)
        self.db.flush()

        count = 0
        for item in rules:
            created = WeightRule(
                weight_grams=max(1, int(item.weight_grams)),
                is_enabled=True,
            )
            self.db.add(created)
            self.db.flush()
            unique_keywords = sorted({keyword.strip().lower() for keyword in item.keywords if keyword and keyword.strip()})
            for keyword in unique_keywords:
                self.db.add(
                    WeightRuleKeyword(
                        rule_id=int(created.id),
                        keyword=keyword,
                    )
                )
            count += 1
        return count

    def _import_brand_mappings(self, rows: list[SettingsTransferBrandMappingEntry]) -> int:
        self.db.query(ParserBrandMapping).delete(synchronize_session=False)
        self.db.flush()
        inserted = 0
        seen_keys: set[str] = set()
        for item in rows:
            key = str(item.source_brand_key or "").strip().lower()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            self.db.add(
                ParserBrandMapping(
                    source_brand=str(item.source_brand).strip(),
                    source_brand_key=key,
                    target_brand=str(item.target_brand).strip(),
                    include_in_designers=bool(item.include_in_designers),
                )
            )
            inserted += 1
        return inserted
