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
    Designer,
    DesignerSourceName,
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
from app.services.catalog.designer_support import slugify_designer_name
from app.services.catalog.source_registry_service import SourceRegistryService
from app.core.source_identity import normalize_base_url
from app.services.settings.pricing_service import PricingSettingsService
from app.schemas.admin_settings import (
    SettingsTransferAdminUiSettings,
    SettingsTransferDesignerSourceNameEntry,
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
    "customs_threshold_eur",
    "customs_duty_rate",
    "eur_to_rub_rate",
    "usd_to_rub_rate",
    "usdt_to_rub_rate",
    "usdt_extra_rub",
    "final_rounding_mode",
    "payment_fee_rate",
    "customs_processing_rate",
    "customs_fixed_rub",
    "tax_rate",
]

_PRICING_IMPORT_FIELDS = set(_PRICING_EXPORT_FIELDS)


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


def _slugify_name(raw: str) -> str:
    return slugify_designer_name(raw)


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

    def export_payload(self) -> SettingsTransferPayload:
        pricing_row, _ = self.pricing_repo.get_or_create_default()
        suppliers = self.supplier_repo.list_all_with_rates()
        sources = SourceRegistryService(self.db).refresh_from_service()
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
            designers_exclude_store_names=bool(getattr(ui_row, "designers_exclude_store_names", False)),
            auto_sync_period_minutes=max(60, int(getattr(ui_row, "auto_sync_period_minutes", 60) or 60)),
        )

        supplier_entries = [
            SettingsTransferSupplierEntry(
                key=str(supplier.key),
                name=str(supplier.name),
                provider_kind=str(getattr(supplier, "provider_kind", "main") or "main"),
                parent_supplier_key=(
                    str(supplier_by_id[int(supplier.parent_supplier_id)].key)
                    if getattr(supplier, "parent_supplier_id", None) is not None
                    and int(supplier.parent_supplier_id) in supplier_by_id
                    else None
                ),
                rate_currency=str(supplier.rate_currency),
                is_enabled=bool(getattr(supplier, "is_enabled", True)),
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

        source_entries: list[SettingsTransferSourceEntry] = []
        for source in sources:
            if str(source.key) == SourceRegistryService.MANUAL_SOURCE_KEY:
                continue
            setting = self.source_repo.ensure_setting(source)
            source_entries.append(
                SettingsTransferSourceEntry(
                    key=str(source.key),
                    name=str(source.name),
                    url=str(source.base_url),
                    enabled=bool(getattr(setting, "is_enabled", True)),
                    sync_enabled=bool(getattr(setting, "is_sync_enabled", True)),
                    dedup_enabled=bool(getattr(setting, "dedup_enabled", True)),
                    hide_auto_added_products=bool(getattr(setting, "hide_auto_added_products", False)),
                    description_mode=str(getattr(setting, "description_mode", "text") or "text"),
                    show_images=bool(getattr(setting, "show_images", True)),
                    supplier_key=(
                        str(supplier_by_id[int(setting.supplier_id)].key)
                        if getattr(setting, "supplier_id", None) is not None and int(setting.supplier_id) in supplier_by_id
                        else None
                    ),
                    promo_factor=float(getattr(setting, "promo_factor", 1.0) or 1.0),
                    promo_only_no_discount=bool(getattr(setting, "promo_only_no_discount", False)),
                    buyout_surcharge_value=(
                        float(setting.buyout_surcharge_value)
                        if getattr(setting, "buyout_surcharge_value", None) is not None
                        else None
                    ),
                    buyout_surcharge_currency=(
                        _normalize_currency(getattr(setting, "buyout_surcharge_currency", None), default="RUB")
                        if getattr(setting, "buyout_surcharge_currency", None)
                        else None
                    ),
                )
            )

        weight_entries = [
            SettingsTransferWeightRuleEntry(
                weight_grams=int(rule.weight_grams),
                keywords=[
                    str(item.keyword)
                    for item in self.weight_rule_repo.list_keywords(int(rule.id))
                ],
            )
            for rule in weight_rules
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
            designer_source_names=[
                SettingsTransferDesignerSourceNameEntry(
                    source_name=str(row.source_name),
                    designer_name=(str(row.designer_name) if row.designer_name is not None else None),
                )
                for row in (
                    self.db.query(DesignerSourceName)
                    .order_by(DesignerSourceName.source_name.asc(), DesignerSourceName.id.asc())
                    .all()
                )
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
        designer_source_names_updated = self._import_designer_source_names(payload.designer_source_names)

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
                "designer_source_names_replaced": designer_source_names_updated,
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
            if str(source.key) == SourceRegistryService.MANUAL_SOURCE_KEY:
                continue
            setting = self.source_repo.ensure_setting(source)
            setting.is_enabled = True
            setting.is_sync_enabled = True
            setting.dedup_enabled = True
            setting.hide_auto_added_products = False
            setting.description_mode = "text"
            setting.show_images = True
            setting.promo_factor = 1.0
            setting.promo_only_no_discount = False
            setting.buyout_surcharge_value = None
            setting.buyout_surcharge_currency = None
            if fallback_supplier is not None:
                setting.supplier_id = int(fallback_supplier.id)
            sources_reset += 1

        # 3) Reset weight rules.
        self.db.query(WeightRuleKeyword).delete(synchronize_session=False)
        self.db.query(WeightRule).delete(synchronize_session=False)

        # 4) Reset designer source names.
        self.db.query(DesignerSourceName).delete(synchronize_session=False)

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
            "eur_to_rub_rate": float(values["eur_to_rub_rate"]),
            "usd_to_rub_rate": float(values["usd_to_rub_rate"]),
            "usdt_to_rub_rate": float(values["usdt_to_rub_rate"]),
            "usdt_extra_rub": float(values["usdt_extra_rub"]),
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
            "designers_exclude_store_names": bool(values.get("designers_exclude_store_names")),
            "auto_sync_period_minutes": max(60, int(values.get("auto_sync_period_minutes") or 60)),
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
                    provider_kind="alternate" if incoming.provider_kind in {"alternate", "alt"} else "main",
                    rate_currency=_normalize_currency(incoming.rate_currency, default="RUB"),
                    is_enabled=bool(incoming.is_enabled),
                )
                self.db.flush()
            else:
                current.name = incoming.name
                current.provider_kind = "alternate" if incoming.provider_kind in {"alternate", "alt"} else "main"
                current.rate_currency = _normalize_currency(incoming.rate_currency, default="RUB")
                current.is_enabled = bool(incoming.is_enabled)
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
            supplier.provider_kind = "alternate" if supplier.parent_supplier_id is not None else "main"
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
        for item in sources:
            source_key = SourceRegistryService.normalize_source_key(item.key)
            if not source_key or source_key == SourceRegistryService.MANUAL_SOURCE_KEY:
                continue
            existing = self.source_repo.get_by_key(source_key)
            if existing is None:
                existing = self.db.query(Source).filter(Source.key == source_key).one_or_none()
            supplier = supplier_map.get(item.supplier_key) if item.supplier_key else None
            if item.supplier_key and supplier is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Для источника '{item.name}' не найден назначенный тариф",
                )
            supplier_id = int(supplier.id) if supplier is not None else None
            if existing is None:
                existing = self.source_repo.create(
                    key=source_key,
                    name=item.name,
                    base_url=item.url,
                )
            else:
                existing.name = item.name
                existing.base_url = item.url
                existing.base_url_normalized = normalize_base_url(item.url)
                existing.host_normalized = SourceRegistryService.normalize_source_key(item.url)
            setting = self.source_repo.ensure_setting(existing)
            setting.is_enabled = bool(item.enabled)
            setting.is_sync_enabled = bool(item.sync_enabled)
            setting.dedup_enabled = bool(item.dedup_enabled)
            setting.hide_auto_added_products = bool(item.hide_auto_added_products)
            setting.description_mode = str(item.description_mode)
            setting.show_images = bool(item.show_images)
            setting.supplier_id = supplier_id
            setting.promo_factor = float(item.promo_factor)
            setting.promo_only_no_discount = bool(item.promo_only_no_discount)
            setting.buyout_surcharge_value = (
                float(item.buyout_surcharge_value)
                if item.buyout_surcharge_value is not None
                else None
            )
            setting.buyout_surcharge_currency = (
                _normalize_currency(item.buyout_surcharge_currency, default="RUB")
                if item.buyout_surcharge_currency is not None
                else None
            )
            updated += 1

            self._service_patch(source_key, {"sync_enabled": bool(item.sync_enabled), "enabled": bool(item.enabled)})
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

    def _import_designer_source_names(self, rows: list[SettingsTransferDesignerSourceNameEntry]) -> int:
        self.db.query(DesignerSourceName).delete(synchronize_session=False)
        self.db.flush()
        inserted = 0
        designers_by_name = {
            str(designer.name).strip(): designer
            for designer in self.db.query(Designer).order_by(Designer.id.asc()).all()
            if str(designer.name or "").strip()
        }
        used_slugs = {
            str(designer.slug).strip()
            for designer in designers_by_name.values()
            if str(designer.slug or "").strip()
        }
        seen_source_names: set[str] = set()

        def next_slug(name: str) -> str:
            base = _slugify_name(name)
            if base not in used_slugs:
                used_slugs.add(base)
                return base
            index = 2
            while f"{base}-{index}" in used_slugs:
                index += 1
            slug = f"{base}-{index}"
            used_slugs.add(slug)
            return slug

        for item in rows:
            source_name = str(item.source_name or "").strip()
            if not source_name:
                continue
            if source_name in seen_source_names:
                continue
            seen_source_names.add(source_name)

            designer_id = None
            designer_name = str(item.designer_name or "").strip()
            if designer_name:
                designer = designers_by_name.get(designer_name)
                if designer is None:
                    designer = Designer(
                        name=designer_name,
                        slug=next_slug(designer_name),
                        origin_kind="manual",
                        is_admin_touched=True,
                        is_enabled=True,
                    )
                    self.db.add(designer)
                    self.db.flush()
                    designers_by_name[designer_name] = designer
                designer_id = int(designer.id)

            self.db.add(
                DesignerSourceName(
                    source_name=source_name,
                    designer_name=(designer_name or source_name),
                    designer_id=designer_id,
                    is_enabled=True,
                    is_admin_touched=True,
                )
            )
            inserted += 1
        return inserted
