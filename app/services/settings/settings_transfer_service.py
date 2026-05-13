"""Export/import of admin settings (without product data)."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import (
    AdminUiSettings,
    ParserBrandMapping,
    ParserCategory,
    ParserCategoryCountSnapshot,
    ParserCategoryIndexState,
    ParserCategoryKeyword,
    ParserCategoryManualProduct,
    ParserProductCategoryMatch,
    ParserSource,
    ParserPricingSettings,
    ParserSupplier,
    ParserSupplierShippingRate,
    ParserWeightKeyword,
    ParserWeightRule,
)
from app.repositories import (
    ParserCategoryKeywordRepository,
    ParserCategoryRepository,
    ParserPricingSettingsRepository,
    ParserSupplierRepository,
    ParserWeightKeywordRepository,
    ParserWeightRuleRepository,
)
from app.services.settings.pricing_service import PricingSettingsService
from app.schemas.parser import (
    SettingsTransferCategoryEntry,
    SettingsTransferCategoryKeywordEntry,
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
    "shipping_rules",
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


class SettingsTransferService:
    """Application service for settings export/import."""

    def __init__(self, db: Session):
        self.db = db
        self.pricing_repo = ParserPricingSettingsRepository(db)
        self.supplier_repo = ParserSupplierRepository(db)
        self.weight_rule_repo = ParserWeightRuleRepository(db)
        self.weight_keyword_repo = ParserWeightKeywordRepository(db)
        self.category_repo = ParserCategoryRepository(db)
        self.category_keyword_repo = ParserCategoryKeywordRepository(db)

    def export_payload(self) -> SettingsTransferPayload:
        pricing_row, _ = self.pricing_repo.get_or_create_default()
        suppliers = self.supplier_repo.list_all_with_rates()
        sources = (
            self.db.query(ParserSource)
            .filter(ParserSource.deleted_at.is_(None))
            .order_by(ParserSource.id.asc())
            .all()
        )
        weight_rules = self.weight_rule_repo.get_all_active()
        categories = self.category_repo.get_all_active()

        supplier_by_id = {int(supplier.id): supplier for supplier in suppliers}
        category_slug_by_id = {int(item.id): item.slug for item in categories}

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
                category=str(supplier.category),
                parent_supplier_key=(
                    str(supplier_by_id[int(supplier.parent_supplier_id)].key)
                    if getattr(supplier, "parent_supplier_id", None) is not None
                    and int(supplier.parent_supplier_id) in supplier_by_id
                    else None
                ),
                alt_position=max(0, int(getattr(supplier, "alt_position", 0) or 0)),
                rate_currency=str(supplier.rate_currency),
                rates=[
                    {
                        "min_kg": float(rate.min_kg),
                        "max_kg": (float(rate.max_kg) if rate.max_kg is not None else None),
                        "rub": float(rate.rate_rub),
                    }
                    for rate in sorted(
                        supplier.shipping_rates,
                        key=lambda item: (float(item.min_kg or 0.0), float(item.max_kg) if item.max_kg is not None else float("inf")),
                    )
                ],
            )
            for supplier in suppliers
        ]

        source_entries = [
            SettingsTransferSourceEntry(
                name=str(source.name),
                url=str(source.url),
                enabled=bool(source.enabled),
                supplier_key=(
                    str(supplier_by_id[int(source.supplier_id)].key)
                    if source.supplier_id is not None and int(source.supplier_id) in supplier_by_id
                    else None
                ),
                promo_factor=float(source.promo_factor),
                promo_only_no_discount=bool(source.promo_only_no_discount),
                buyout_surcharge_value=float(source.buyout_surcharge_value),
                buyout_surcharge_currency=_normalize_currency(source.buyout_surcharge_currency, default="RUB"),
            )
            for source in sources
        ]

        weight_entries = [
            SettingsTransferWeightRuleEntry(
                weight_grams=int(rule.weight_grams),
                sort_order=int(rule.sort_order),
                keywords=[
                    str(item.keyword)
                    for item in self.weight_keyword_repo.get_by_rule(int(rule.id))
                ],
            )
            for rule in weight_rules
        ]

        category_entries = [
            SettingsTransferCategoryEntry(
                slug=str(category.slug),
                name=str(category.name),
                parent_slug=(
                    str(category_slug_by_id[int(category.parent_id)])
                    if category.parent_id is not None and int(category.parent_id) in category_slug_by_id
                    else None
                ),
                is_fallback=bool(category.is_fallback),
                is_favorite=bool(category.is_favorite),
                is_enabled=bool(category.is_enabled),
            )
            for category in categories
        ]
        category_keyword_entries: list[SettingsTransferCategoryKeywordEntry] = []
        for category in categories:
            for keyword in self.category_keyword_repo.get_by_category(int(category.id)):
                category_keyword_entries.append(
                    SettingsTransferCategoryKeywordEntry(
                        category_slug=str(category.slug),
                        keyword=str(keyword.keyword),
                        scope=str(keyword.keyword_scope),
                    )
                )

        return SettingsTransferPayload(
            schema_version=_SCHEMA_VERSION,
            exported_at=datetime.now(timezone.utc).isoformat(),
            project=_PROJECT_NAME,
            pricing_settings=pricing,
            admin_ui_settings=admin_ui,
            suppliers=supplier_entries,
            sources=source_entries,
            weight_rules=weight_entries,
            categories=category_entries,
            category_keywords=category_keyword_entries,
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
        categories_updated, keywords_updated = self._import_categories(payload.categories, payload.category_keywords)

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
                "categories_upserted": categories_updated,
                "category_keywords_replaced": keywords_updated,
            },
        )

    def reset_all(self) -> SettingsTransferResponse:
        # 1) Reset pricing settings/suppliers to service defaults.
        self.db.query(ParserSupplierShippingRate).delete(synchronize_session=False)
        self.db.query(ParserSupplier).delete(synchronize_session=False)
        self.db.query(ParserPricingSettings).delete(synchronize_session=False)
        self.db.query(AdminUiSettings).delete(synchronize_session=False)
        self.db.flush()

        pricing_service = PricingSettingsService(self.db)
        pricing_service.get_settings(refresh_bybit=False)
        suppliers = self.supplier_repo.list_all_with_rates()
        fallback_supplier = next((s for s in suppliers if str(getattr(s, "category", "")) == "main"), suppliers[0] if suppliers else None)

        # 2) Reset sources to neutral defaults.
        sources_reset = 0
        for source in self.db.query(ParserSource).filter(ParserSource.deleted_at.is_(None)).all():
            source.enabled = True
            source.hide_auto_added_products = False
            source.promo_factor = 1.0
            source.promo_only_no_discount = False
            source.buyout_surcharge_value = 0.0
            source.buyout_surcharge_currency = "RUB"
            if fallback_supplier is not None:
                source.supplier_id = int(fallback_supplier.id)
            sources_reset += 1

        # 3) Reset weight rules.
        self.db.query(ParserWeightKeyword).delete(synchronize_session=False)
        self.db.query(ParserWeightRule).delete(synchronize_session=False)

        # 4) Reset categories customizations.
        self.db.query(ParserCategoryKeyword).delete(synchronize_session=False)
        self.db.query(ParserCategoryManualProduct).delete(synchronize_session=False)
        self.db.query(ParserProductCategoryMatch).delete(synchronize_session=False)
        self.db.query(ParserCategoryCountSnapshot).delete(synchronize_session=False)
        self.db.query(ParserCategoryIndexState).delete(synchronize_session=False)
        categories_reset = 0
        for category in self.db.query(ParserCategory).filter(ParserCategory.deleted_at.is_(None)).all():
            category.is_favorite = False
            category.is_enabled = True
            categories_reset += 1

        # 5) Reset designers remapping.
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
                "categories_upserted": categories_reset,
                "category_keywords_replaced": 0,
            },
        )

    def _import_pricing(self, payload: SettingsTransferPricingSettings) -> int:
        row, _ = self.pricing_repo.get_or_create_default()
        values = payload.model_dump()
        updated_fields = 0
        for key, raw_value in values.items():
            if key not in _PRICING_IMPORT_FIELDS:
                continue
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
            "designers_exclude_store_vendors": bool(values.get("designers_exclude_store_vendors")),
            "showcase_hero_image_asset_id": int(values["showcase_hero_image_asset_id"]) if isinstance(values.get("showcase_hero_image_asset_id"), int) and int(values.get("showcase_hero_image_asset_id")) > 0 else None,
            "showcase_carousel_image_asset_ids": PricingSettingsService._normalize_image_asset_ids(values.get("showcase_carousel_image_asset_ids"), limit=20),
        }
        for key, raw_value in normalized.items():
            if getattr(row, key) != raw_value:
                setattr(row, key, raw_value)
                updated_fields += 1
        return updated_fields

    def _import_suppliers(self, suppliers: list[SettingsTransferSupplierEntry]) -> dict[str, ParserSupplier]:
        existing = {str(item.key): item for item in self.supplier_repo.list_all_with_rates()}
        result: dict[str, ParserSupplier] = {}
        incoming_by_key: dict[str, SettingsTransferSupplierEntry] = {}

        for index, incoming in enumerate(suppliers, start=1):
            key = _normalize_supplier_key(incoming.key, incoming.name, index=index)
            incoming_by_key[key] = incoming
            current = existing.get(key)
            if current is None:
                current = self.supplier_repo.create(
                    key=key,
                    name=incoming.name,
                    category=incoming.category if incoming.category in {"main", "alt"} else "main",
                    rate_currency=_normalize_currency(incoming.rate_currency, default="RUB"),
                )
                self.db.flush()
            else:
                current.name = incoming.name
                current.category = incoming.category if incoming.category in {"main", "alt"} else "main"
                current.rate_currency = _normalize_currency(incoming.rate_currency, default="RUB")

            self.db.query(ParserSupplierShippingRate).filter(ParserSupplierShippingRate.supplier_id == current.id).delete(
                synchronize_session=False
            )
            for rate in incoming.rates:
                min_kg = max(0.0, float(getattr(rate, "min_kg", 0.0)))
                max_raw = getattr(rate, "max_kg", None)
                max_kg = None if max_raw is None else max(min_kg + 0.000001, float(max_raw))
                self.db.add(
                    ParserSupplierShippingRate(
                        supplier_id=int(current.id),
                        min_kg=min_kg,
                        max_kg=max_kg,
                        rate_rub=max(0.0, float(getattr(rate, "rub", 0.0))),
                    )
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
            if supplier.parent_supplier_id is not None:
                supplier.alt_position = max(1, int(getattr(incoming, "alt_position", 1) or 1))
                supplier.category = "alt"
            else:
                supplier.alt_position = 0
                if supplier.category not in {"main", "alt"}:
                    supplier.category = "main"
        return result

    def _import_sources(
        self,
        sources: list[SettingsTransferSourceEntry],
        *,
        supplier_map: dict[str, ParserSupplier],
    ) -> int:
        if not supplier_map:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нет тарифов для назначения источникам")
        fallback_supplier = next(iter(supplier_map.values()))
        updated = 0

        for item in sources:
            existing = (
                self.db.query(ParserSource)
                .filter(ParserSource.deleted_at.is_(None))
                .filter(ParserSource.url == item.url)
                .first()
            )
            supplier = (
                supplier_map.get(item.supplier_key)
                if item.supplier_key
                else None
            )
            supplier_id = int((supplier or fallback_supplier).id)
            if existing is None:
                self.db.add(
                    ParserSource(
                        name=item.name,
                        url=item.url,
                        enabled=bool(item.enabled),
                        supplier_id=supplier_id,
                        promo_factor=float(item.promo_factor),
                        promo_only_no_discount=bool(item.promo_only_no_discount),
                        buyout_surcharge_value=float(item.buyout_surcharge_value),
                        buyout_surcharge_currency=_normalize_currency(item.buyout_surcharge_currency, default="RUB"),
                    )
                )
            else:
                existing.name = item.name
                existing.enabled = bool(item.enabled)
                existing.supplier_id = supplier_id
                existing.promo_factor = float(item.promo_factor)
                existing.promo_only_no_discount = bool(item.promo_only_no_discount)
                existing.buyout_surcharge_value = float(item.buyout_surcharge_value)
                existing.buyout_surcharge_currency = _normalize_currency(item.buyout_surcharge_currency, default="RUB")
            updated += 1
        return updated

    def _import_weight_rules(self, rules: list[SettingsTransferWeightRuleEntry]) -> int:
        self.db.query(ParserWeightKeyword).delete(synchronize_session=False)
        self.db.query(ParserWeightRule).delete(synchronize_session=False)
        self.db.flush()

        count = 0
        for index, item in enumerate(rules):
            created = ParserWeightRule(
                weight_grams=max(1, int(item.weight_grams)),
                sort_order=int(item.sort_order if item.sort_order >= 0 else index),
            )
            self.db.add(created)
            self.db.flush()
            unique_keywords = sorted({keyword.strip().lower() for keyword in item.keywords if keyword and keyword.strip()})
            for keyword in unique_keywords:
                self.db.add(
                    ParserWeightKeyword(
                        rule_id=int(created.id),
                        keyword=keyword,
                    )
                )
            count += 1
        return count

    def _import_categories(
        self,
        categories: list[SettingsTransferCategoryEntry],
        keywords: list[SettingsTransferCategoryKeywordEntry],
    ) -> tuple[int, int]:
        existing_categories = {str(item.slug): item for item in self.category_repo.get_all_active()}
        upserted: dict[str, ParserCategory] = {}
        pending_parent: dict[str, str | None] = {}

        ordered = sorted(
            categories,
            key=lambda item: (
                0 if item.parent_slug in (None, "") else 1,
                str(item.parent_slug or ""),
                str(item.slug),
            ),
        )
        for item in ordered:
            slug = item.slug.strip()
            if not slug:
                continue
            current = existing_categories.get(slug)
            if current is None:
                current = self.category_repo.create(
                    slug=slug,
                    name=item.name.strip() or slug,
                    is_fallback=bool(item.is_fallback),
                    is_favorite=bool(item.is_favorite),
                    is_enabled=bool(item.is_enabled),
                    parent_id=None,
                )
                self.db.flush()
            else:
                current.name = item.name.strip() or slug
                current.is_fallback = bool(item.is_fallback)
                current.is_favorite = bool(item.is_favorite)
                current.is_enabled = bool(item.is_enabled)

            upserted[slug] = current
            pending_parent[slug] = item.parent_slug

        for slug, parent_slug in pending_parent.items():
            current = upserted[slug]
            parent = upserted.get(parent_slug or "")
            current.parent_id = int(parent.id) if parent is not None and parent.id != current.id else None

        category_ids = [int(item.id) for item in upserted.values()]
        if category_ids:
            self.db.query(ParserCategoryKeyword).filter(ParserCategoryKeyword.category_id.in_(category_ids)).delete(
                synchronize_session=False
            )
        keyword_count = 0
        seen: set[tuple[int, str, str]] = set()
        for item in keywords:
            category = upserted.get(item.category_slug)
            if category is None:
                continue
            keyword = item.keyword.strip().lower()
            if not keyword:
                continue
            dedupe_key = (int(category.id), keyword, item.scope)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            self.db.add(
                ParserCategoryKeyword(
                    category_id=int(category.id),
                    keyword=keyword,
                    keyword_scope="title" if item.scope == "title" else "local",
                )
            )
            keyword_count += 1
        return len(upserted), keyword_count
