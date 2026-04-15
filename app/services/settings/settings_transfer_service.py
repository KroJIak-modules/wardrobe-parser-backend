"""Export/import of admin settings (without product data)."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import (
    ParserCategory,
    ParserCategoryKeyword,
    ParserSource,
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
from app.schemas.parser import (
    SettingsTransferCategoryEntry,
    SettingsTransferCategoryKeywordEntry,
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
    "final_rounding_mode",
    "payment_fee_rate",
    "customs_processing_rate",
    "customs_fixed_rub",
    "shipping_alt_threshold_eur",
    "tax_rate",
    "svc_rules",
    "insurance_rules",
    "service_fee_rules",
    "shipping_rules",
]

_PRICING_IMPORT_FIELDS = set(_PRICING_EXPORT_FIELDS)


def _normalize_currency(raw: str | None, *, default: str = "RUB") -> str:
    value = (raw or default).strip().upper()
    if value not in {"RUB", "USD", "EUR", "GBP"}:
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

        supplier_entries = [
            SettingsTransferSupplierEntry(
                key=str(supplier.key),
                name=str(supplier.name),
                category=str(supplier.category),
                rate_currency=str(supplier.rate_currency),
                rates=[
                    {
                        "step_500g": int(rate.step_500g),
                        "rate_rub": float(rate.rate_rub),
                    }
                    for rate in sorted(supplier.shipping_rates, key=lambda item: int(item.step_500g))
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
                "suppliers_upserted": len(supplier_map),
                "sources_upserted": source_count,
                "weight_rules_replaced": weight_count,
                "categories_upserted": categories_updated,
                "category_keywords_replaced": keywords_updated,
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

    def _import_suppliers(self, suppliers: list[SettingsTransferSupplierEntry]) -> dict[str, ParserSupplier]:
        existing = {str(item.key): item for item in self.supplier_repo.list_all_with_rates()}
        result: dict[str, ParserSupplier] = {}

        for index, incoming in enumerate(suppliers, start=1):
            key = _normalize_supplier_key(incoming.key, incoming.name, index=index)
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
                step = max(1, int(rate["step_500g"]))
                self.db.add(
                    ParserSupplierShippingRate(
                        supplier_id=int(current.id),
                        step_500g=step,
                        rate_rub=max(0.0, float(rate["rate_rub"])),
                    )
                )
            result[key] = current
        return result

    def _import_sources(
        self,
        sources: list[SettingsTransferSourceEntry],
        *,
        supplier_map: dict[str, ParserSupplier],
    ) -> int:
        if not supplier_map:
            default_supplier = self.supplier_repo.get_default_supplier()
            supplier_map = {"default": default_supplier}
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
