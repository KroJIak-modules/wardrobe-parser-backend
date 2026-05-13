"""Pricing settings CRUD and final price calculation by TZ formula."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any
import requests

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.models import AdminUiSettings, ParserSource, ParserSupplier
from app.repositories import ParserPricingSettingsRepository, ParserSourceRepository, ParserSupplierRepository
from app.schemas.parser import (
    AdminUiSettingsResponse,
    AdminUiSettingsUpdateRequest,
    PricingSettingsResponse,
    PricingSettingsUpdateRequest,
    PricingSupplierCreateRequest,
    PricingSupplierResponse,
    PricingSupplierUpdateRequest,
)
from app.services.settings.bybit_rate_provider import BybitP2PRateProvider

_FORMULA_LINES = [
    "BFX = BBR + BEX",
    "SPU = convert(SP, USD/EUR/GBP -> USD)",
    "SPR = SPU * BFX",
    "BUY = SPR * PRM + BSC",
    "PFR = BUY * PFRP",
    "INS = insurance tier by SPE",
    "CDR = ((max(0, SPE - THR) * DUT) * (1 + CPR)) * (E2U * BFX) + CFX",
    "SVC = configurable surcharge by BUY (fixed RUB or percent)",
    "SUB = BUY + PFR + INS + CDR + SSR[SUP,RNG]",
    "SUBM = SUB * (1 + MUP) + SVC",
    "TAX = SUBM * TXR",
    "FPR = round(SUBM + TAX, RND)",
]

_FORMULA_LATEX = (
    r"\operatorname{round}_{RND}\!\left(\left(\left((SPU\cdot(BBR+BEX)\cdot PRM+BSC)+((SPU\cdot(BBR+BEX)\cdot PRM+BSC)\cdot PFRP)+INS+\left((\max\!\left(0,SPE-THR\right)\cdot DUT)\cdot(1+CPR)\cdot(E2U\cdot(BBR+BEX))+CFX\right)+SSR[SUP,RNG]\right)\cdot(1+MUP)+SVC\right)+\left(\left(\left((SPU\cdot(BBR+BEX)\cdot PRM+BSC)+((SPU\cdot(BBR+BEX)\cdot PRM+BSC)\cdot PFRP)+INS+\left((\max\!\left(0,SPE-THR\right)\cdot DUT)\cdot(1+CPR)\cdot(E2U\cdot(BBR+BEX))+CFX\right)+SSR[SUP,RNG]\right)\cdot(1+MUP)+SVC\right)\cdot TXR\right)\right)"
)

_FORMULA_LEGEND = [
    {"key": "SP", "description": "Цена товара в исходной валюте магазина."},
    {"key": "SPU", "description": "Цена товара в USD."},
    {"key": "SPE", "description": "Цена товара в EUR (для таможни и страхования)."},
    {"key": "SPR", "description": "Цена товара в RUB по курсу Bybit."},
    {"key": "BBR", "description": "Курс первого адекватного Bybit-ордера (единый для всех товаров, USDT/RUB)."},
    {"key": "BEX", "description": "Надбавка к курсу Bybit."},
    {"key": "BFX", "description": "Итоговый курс USDT/RUB: BBR + BEX."},
    {"key": "E2U", "description": "Коэффициент EUR -> USD."},
    {"key": "G2U", "description": "Коэффициент GBP -> USD."},
    {"key": "J2U", "description": "Коэффициент JPY -> USD."},
    {"key": "PRM", "description": "Промо-коэффициент источника."},
    {"key": "BSC", "description": "Доплата к выкупу."},
    {"key": "BUY", "description": "Стоимость выкупа товара в RUB."},
    {"key": "PFRP", "description": "Ставка комиссии платежки."},
    {"key": "PFR", "description": "Комиссия платежки в RUB."},
    {"key": "THR", "description": "Порог таможни в EUR."},
    {"key": "DUT", "description": "Ставка пошлины на превышение порога."},
    {"key": "CPR", "description": "Ставка обработки пошлины."},
    {"key": "CFX", "description": "Фиксированная часть таможни в RUB."},
    {"key": "CDR", "description": "Таможня в RUB."},
    {"key": "SSR", "description": "Доставка поставщика."},
    {"key": "SUP", "description": "Поставщик."},
    {"key": "RNG", "description": "Весовой диапазон тарифа доставки."},
    {"key": "INS", "description": "Страховка в RUB."},
    {"key": "SVC", "description": "Пользовательская надбавка сервиса в RUB (фикс или % от BUY)."},
    {"key": "SUB", "description": "База до наценки и SVC: BUY + PFR + INS + CDR + SSR."},
    {"key": "SUBM", "description": "Сумма до налога: SUB * (1 + MUP) + SVC."},
    {"key": "TXR", "description": "Ставка налога."},
    {"key": "TAX", "description": "Налог в RUB."},
    {"key": "MUP", "description": "Наценка (доля к SUB, например 0.25 = +25%)."},
    {"key": "RND", "description": "Режим округления финальной цены."},
    {"key": "FPR", "description": "Финальная цена в RUB."},
]

_DEFAULT_INSURANCE_RULES: list[dict[str, Any]] = [
    {"min_eur": 0.0, "max_eur": 300.0, "mode": "percent", "value": 0.01},
    {"min_eur": 300.0, "max_eur": 520.0, "mode": "fixed_rub", "value": 1000.0},
    {"min_eur": 520.0, "max_eur": None, "mode": "fixed_rub", "value": 1300.0},
]

_DEFAULT_SERVICE_FEE_RULES: list[dict[str, Any]] = [
    {"min_rub": 0.0, "max_rub": 7000.0, "mode": "percent", "value": 0.25},
    {"min_rub": 7000.0, "max_rub": 10000.0, "mode": "fixed_rub", "value": 2500.0},
    {"min_rub": 10000.0, "max_rub": 17000.0, "mode": "fixed_rub", "value": 3000.0},
    {"min_rub": 17000.0, "max_rub": 20000.0, "mode": "fixed_rub", "value": 3500.0},
    {"min_rub": 20000.0, "max_rub": 30000.0, "mode": "percent", "value": 0.20},
    {"min_rub": 30000.0, "max_rub": 40000.0, "mode": "fixed_rub", "value": 6000.0},
    {"min_rub": 40000.0, "max_rub": None, "mode": "percent", "value": 0.15},
]

_DEFAULT_SHIPPING_RULES: dict[str, dict[str, list[dict[str, Any]]]] = {
    "US": {
        "normal": [
            {"min_kg": 0.0, "max_kg": 0.5, "rub": 1400.0},
            {"min_kg": 0.5, "max_kg": 1.0, "rub": 1650.0},
            {"min_kg": 1.0, "max_kg": 1.5, "rub": 2250.0},
            {"min_kg": 1.5, "max_kg": 2.0, "rub": 2900.0},
            {"min_kg": 2.0, "max_kg": 2.5, "rub": 3500.0},
            {"min_kg": 2.5, "max_kg": None, "rub": 4100.0},
        ],
        "alt": [
            {"min_kg": 0.0, "max_kg": 0.5, "rub": 1700.0},
            {"min_kg": 0.5, "max_kg": 1.0, "rub": 3350.0},
            {"min_kg": 1.0, "max_kg": 1.5, "rub": 4100.0},
            {"min_kg": 1.5, "max_kg": 2.0, "rub": 4950.0},
            {"min_kg": 2.0, "max_kg": 2.5, "rub": 5650.0},
            {"min_kg": 2.5, "max_kg": None, "rub": 6500.0},
        ],
    },
    "EU": {
        "normal": [
            {"min_kg": 0.0, "max_kg": 0.5, "rub": 1100.0},
            {"min_kg": 0.5, "max_kg": 1.0, "rub": 1500.0},
            {"min_kg": 1.0, "max_kg": 1.5, "rub": 1900.0},
            {"min_kg": 1.5, "max_kg": 2.0, "rub": 2300.0},
            {"min_kg": 2.0, "max_kg": 2.5, "rub": 2700.0},
            {"min_kg": 2.5, "max_kg": None, "rub": 3150.0},
        ],
        "alt": [
            {"min_kg": 0.0, "max_kg": 0.5, "rub": 2300.0},
            {"min_kg": 0.5, "max_kg": 1.0, "rub": 2750.0},
            {"min_kg": 1.0, "max_kg": 1.5, "rub": 3750.0},
            {"min_kg": 1.5, "max_kg": 2.0, "rub": 4800.0},
            {"min_kg": 2.0, "max_kg": 2.5, "rub": 5800.0},
            {"min_kg": 2.5, "max_kg": None, "rub": 6800.0},
        ],
    },
    "UK": {
        "normal": [
            {"min_kg": 0.0, "max_kg": 0.5, "rub": 3400.0},
            {"min_kg": 0.5, "max_kg": 1.0, "rub": 3900.0},
            {"min_kg": 1.0, "max_kg": 1.5, "rub": 4400.0},
            {"min_kg": 1.5, "max_kg": 2.0, "rub": 4900.0},
            {"min_kg": 2.0, "max_kg": 2.5, "rub": 5450.0},
            {"min_kg": 2.5, "max_kg": None, "rub": 5950.0},
        ],
        "alt": [],
    },
}

_PRODUCTION_SUPPLIER_PRESETS: list[dict[str, Any]] = [
    {
        "legacy_keys": ["us-express-test", "us-main", "default"],
        "key": "usa",
        "name": "США",
        "category": "main",
        "rate_currency": "RUB",
    },
    {
        "legacy_keys": ["us-alt", "usa-alt-1"],
        "key": "usa-alt-1",
        "name": "ALT 1 США",
        "category": "alt",
        "parent_key": "usa",
        "alt_position": 1,
        "rate_currency": "RUB",
    },
    {
        "legacy_keys": ["eu-priority-test", "eu-main"],
        "key": "eu",
        "name": "ЕС",
        "category": "main",
        "rate_currency": "RUB",
    },
    {
        "legacy_keys": ["eu-economy-test", "eu-alt", "eu-alt-1"],
        "key": "eu-alt-1",
        "name": "ALT 1 ЕС",
        "category": "alt",
        "parent_key": "eu",
        "alt_position": 1,
        "rate_currency": "RUB",
    },
    {
        "legacy_keys": ["uk-main", "gb-main"],
        "key": "uk",
        "name": "Великобритания",
        "category": "main",
        "rate_currency": "RUB",
    },
]

@dataclass(slots=True)
class ProductPricingComputation:
    final_price_rub: float | None
    manual_required: bool
    reason: str | None
    components: dict[str, Any]


class PricingSettingsService:
    """Manage pricing settings and calculate final customer price."""
    _SUPPORTED_CURRENCIES = {"RUB", "USD", "EUR", "GBP", "JPY"}

    def __init__(self, db: Session):
        self.db = db
        self.repo = ParserPricingSettingsRepository(db)
        self.supplier_repo = ParserSupplierRepository(db)
        self.source_repo = ParserSourceRepository(db)

    def _bootstrap_suppliers(self) -> bool:
        changed = False
        created_or_matched: dict[str, ParserSupplier] = {}
        for preset in _PRODUCTION_SUPPLIER_PRESETS:
            canonical = self.supplier_repo.get_by_key(str(preset["key"]))
            legacy_items = [
                item
                for item in (self.supplier_repo.get_by_key(str(raw_key)) for raw_key in preset["legacy_keys"])
                if item is not None
            ]
            target = canonical or (legacy_items[0] if legacy_items else None)
            if target is None:
                target = self.supplier_repo.create(
                    key=str(preset["key"]),
                    name=str(preset["name"]),
                    category=self._normalize_supplier_category(str(preset.get("category") or "main")),
                    rate_currency=str(preset["rate_currency"]),
                )
                self.supplier_repo.flush()
                changed = True

            for legacy in legacy_items:
                if legacy.id == target.id:
                    continue
                self.db.query(ParserSource).filter(ParserSource.supplier_id == legacy.id).update(
                    {ParserSource.supplier_id: target.id},
                    synchronize_session=False,
                )
                self.db.delete(legacy)
                changed = True

            # One-time key migration from legacy key to canonical key.
            if canonical is None and target.key != preset["key"]:
                target.key = str(preset["key"])
                changed = True
            created_or_matched[str(preset["key"])] = target

        for preset in _PRODUCTION_SUPPLIER_PRESETS:
            target = created_or_matched[str(preset["key"])]
            if preset.get("category") == "alt":
                parent = created_or_matched.get(str(preset.get("parent_key") or ""))
                parent_id = int(parent.id) if parent is not None else None
                if parent_id is not None and getattr(target, "parent_supplier_id", None) != parent_id:
                    target.parent_supplier_id = parent_id
                    changed = True
                desired_pos = int(preset.get("alt_position") or 1)
                if int(getattr(target, "alt_position", 0) or 0) != desired_pos:
                    target.alt_position = desired_pos
                    changed = True
            else:
                if getattr(target, "parent_supplier_id", None) is not None:
                    target.parent_supplier_id = None
                    changed = True
                if int(getattr(target, "alt_position", 0) or 0) != 0:
                    target.alt_position = 0
                    changed = True

        return changed

    @staticmethod
    def _normalize_currency(raw: str | None, *, default: str = "RUB", allowed: set[str] | None = None) -> str:
        value = (raw or default).strip().upper()
        allowed_set = allowed if allowed is not None else PricingSettingsService._SUPPORTED_CURRENCIES
        if value not in allowed_set:
            return default
        return value

    @staticmethod
    def _fetch_jpy_to_usd_rate() -> float:
        response = requests.get("https://api.frankfurter.app/latest?from=JPY&to=USD", timeout=6.0)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("invalid FX payload")
        rates = payload.get("rates")
        if not isinstance(rates, dict):
            raise RuntimeError("invalid FX payload")
        value = float(rates.get("USD"))
        if value <= 0:
            raise RuntimeError("invalid JPY->USD rate")
        return value

    @staticmethod
    def _normalize_supplier_category(raw: str | None, *, default: str = "main") -> str:
        value = (raw or default).strip().lower()
        if value not in {"main", "alt"}:
            return default
        return value

    @staticmethod
    def _validate_alt_parent(parent: ParserSupplier | None) -> ParserSupplier:
        if parent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Базовый тариф не найден")
        if getattr(parent, "parent_supplier_id", None) is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ALT можно привязать только к базовому тарифу")
        return parent

    def _validate_alt_limit(self, parent_supplier_id: int, *, ignore_supplier_id: int | None = None) -> None:
        query = self.supplier_repo.query().filter(ParserSupplier.parent_supplier_id == int(parent_supplier_id))
        if ignore_supplier_id is not None:
            query = query.filter(ParserSupplier.id != int(ignore_supplier_id))
        count = int(query.count())
        if count >= 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="У базового тарифа может быть только 1 альтернатива")

    @staticmethod
    def _normalize_svc_rule_mode(raw: str | None, *, default: str = "fixed_rub") -> str:
        value = (raw or default).strip().lower()
        if value not in {"fixed_rub", "percent"}:
            return default
        return value

    @staticmethod
    def _normalize_final_rounding_mode(raw: str | None, *, default: str = "unit") -> str:
        value = (raw or default).strip().lower()
        if value not in {"none", "unit", "ten", "hundred", "thousand"}:
            return default
        return value

    @staticmethod
    def _apply_final_rounding(value: float, mode: str) -> float:
        safe_value = float(value)
        normalized_mode = PricingSettingsService._normalize_final_rounding_mode(mode)
        if normalized_mode == "none":
            return safe_value
        if normalized_mode == "unit":
            return float(math.ceil(safe_value))
        step_map = {
            "ten": 10.0,
            "hundred": 100.0,
            "thousand": 1000.0,
        }
        step = float(step_map.get(normalized_mode, 1.0))
        return float(math.ceil(safe_value / step) * step)

    @classmethod
    def _normalize_svc_rules(cls, raw_rules: Any) -> list[dict[str, float | str]]:
        if not isinstance(raw_rules, list):
            return []
        normalized: list[dict[str, float | str]] = []
        for row in raw_rules:
            if not isinstance(row, dict):
                continue
            min_rub = cls._safe_float(row.get("min_rub"))
            max_rub = cls._safe_float(row.get("max_rub"))
            value = cls._safe_float(row.get("value"))
            if min_rub is None or value is None:
                continue
            min_rub = max(0.0, float(min_rub))
            max_rub_value = None if max_rub is None else max(0.0, float(max_rub))
            if max_rub_value is not None and max_rub_value <= min_rub:
                continue
            normalized.append(
                {
                    "min_rub": min_rub,
                    "max_rub": max_rub_value,
                    "mode": cls._normalize_svc_rule_mode(str(row.get("mode") or "fixed_rub")),
                    "value": max(0.0, float(value)),
                }
            )
        normalized.sort(
            key=lambda item: (
                float(item["min_rub"]),
                float(item["max_rub"]) if item.get("max_rub") is not None else float("inf"),
            )
        )
        return normalized

    @staticmethod
    def _validate_svc_rules_no_overlap(rules: list[dict[str, float | str]]) -> None:
        previous_max: float | None = None
        for item in rules:
            min_rub = float(item["min_rub"])
            max_raw = item.get("max_rub")
            max_rub = None if max_raw is None else float(max_raw)
            if max_rub is not None and max_rub <= min_rub:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="SVC: конец диапазона должен быть больше начала",
                )
            if previous_max is not None and min_rub < previous_max:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="SVC: диапазоны пересекаются, укажи непересекающиеся интервалы",
                )
            previous_max = max_rub

    @staticmethod
    def _sanitize_svc_rule_boundaries(rules: list[dict[str, float | str]]) -> list[dict[str, float | str]]:
        sanitized: list[dict[str, float | str]] = []
        for item in sorted(
            rules,
            key=lambda row: (
                float(row["min_rub"]),
                float(row["max_rub"]) if row.get("max_rub") is not None else float("inf"),
            ),
        ):
            min_rub = max(0.0, float(item["min_rub"]))
            max_raw = item.get("max_rub")
            max_rub = None if max_raw is None else max(0.0, float(max_raw))
            if max_rub is not None and max_rub <= min_rub:
                continue
            sanitized.append(
                {
                    "min_rub": min_rub,
                    "max_rub": max_rub,
                    "mode": PricingSettingsService._normalize_svc_rule_mode(str(item.get("mode") or "fixed_rub")),
                    "value": max(0.0, float(item.get("value") or 0.0)),
                }
            )
        return sanitized

    @staticmethod
    def _to_rub(value: float, currency: str, *, usd_to_rub: float, eur_to_rub: float) -> float:
        normalized = PricingSettingsService._normalize_currency(currency)
        if normalized == "RUB":
            return float(value)
        if normalized == "USD":
            return float(value) * float(usd_to_rub)
        return float(value) * float(eur_to_rub)

    @staticmethod
    def _from_rub(value_rub: float, currency: str, *, usd_to_rub: float, eur_to_rub: float) -> float:
        normalized = PricingSettingsService._normalize_currency(currency)
        if normalized == "RUB":
            return float(value_rub)
        if normalized == "USD":
            return float(value_rub) / float(usd_to_rub) if usd_to_rub > 0 else 0.0
        return float(value_rub) / float(eur_to_rub) if eur_to_rub > 0 else 0.0

    @staticmethod
    def _effective_fx_rates(
        *,
        bybit_usdt_to_rub: float,
        bybit_extra_rub: float,
        eur_to_usd_rate: float,
    ) -> tuple[float, float]:
        usd_to_rub = max(0.0, float(bybit_usdt_to_rub) + float(bybit_extra_rub))
        eur_to_rub = max(0.0, float(eur_to_usd_rate) * usd_to_rub)
        return usd_to_rub, eur_to_rub

    @classmethod
    def _effective_rates_from_entity(cls, entity) -> tuple[float, float]:
        bybit = float(getattr(entity, "bybit_usdt_to_rub", 95.0) or 95.0)
        extra = float(getattr(entity, "bybit_extra_rub", 1.0) or 1.0)
        eur_to_usd = float(getattr(entity, "eur_to_usd_rate", 1.18) or 1.18)
        return cls._effective_fx_rates(
            bybit_usdt_to_rub=bybit,
            bybit_extra_rub=extra,
            eur_to_usd_rate=eur_to_usd,
        )

    @classmethod
    def _effective_rates_from_settings(cls, settings: PricingSettingsResponse) -> tuple[float, float]:
        return cls._effective_fx_rates(
            bybit_usdt_to_rub=float(settings.bybit_usdt_to_rub),
            bybit_extra_rub=float(settings.bybit_extra_rub),
            eur_to_usd_rate=float(settings.eur_to_usd_rate),
        )

    @staticmethod
    def _normalize_rule_mode(raw_mode: str | None, *, default: str = "fixed_rub") -> str:
        mode = (raw_mode or default).strip().lower()
        if mode not in {"fixed_rub", "percent"}:
            return default
        return mode

    @staticmethod
    def _normalize_range_rules(
        raw_rules: Any,
        *,
        min_key: str,
        max_key: str,
        default_rules: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_rules, list):
            raw_rules = default_rules
        normalized: list[dict[str, Any]] = []
        for row in raw_rules:
            if not isinstance(row, dict):
                continue
            min_raw = PricingSettingsService._safe_float(row.get(min_key))
            max_raw = PricingSettingsService._safe_float(row.get(max_key))
            mode = PricingSettingsService._normalize_rule_mode(str(row.get("mode") or "fixed_rub"))
            value = max(0.0, float(PricingSettingsService._safe_float(row.get("value")) or 0.0))
            normalized.append(
                {
                    min_key: min_raw if min_raw is None else float(min_raw),
                    max_key: max_raw if max_raw is None else float(max_raw),
                    "mode": mode,
                    "value": value,
                }
            )
        if not normalized:
            return [dict(item) for item in default_rules]
        normalized.sort(
            key=lambda item: (
                float(item.get(min_key) or 0.0),
                float(item.get(max_key) or float("inf")) if item.get(max_key) is not None else float("inf"),
            )
        )
        return normalized

    @staticmethod
    def _normalize_shipping_rows(rows: Any) -> list[dict[str, Any]]:
        if not isinstance(rows, list):
            return []
        normalized: list[dict[str, Any]] = []
        legacy_thresholds: list[tuple[float, float]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            min_kg = PricingSettingsService._safe_float(row.get("min_kg"))
            max_kg = PricingSettingsService._safe_float(row.get("max_kg"))
            kg = PricingSettingsService._safe_float(row.get("kg"))
            rub = PricingSettingsService._safe_float(row.get("rub"))
            safe_rub = max(0.0, float(rub or 0.0))
            if min_kg is not None or max_kg is not None:
                safe_min = max(0.0, float(min_kg or 0.0))
                safe_max = None if max_kg is None else max(0.0, float(max_kg))
                if safe_max is not None and safe_max <= safe_min:
                    continue
                normalized.append({"min_kg": safe_min, "max_kg": safe_max, "rub": safe_rub})
                continue
            if kg is not None and kg > 0:
                legacy_thresholds.append((float(kg), safe_rub))
        if not normalized and legacy_thresholds:
            legacy_thresholds.sort(key=lambda item: item[0])
            previous_kg = 0.0
            for threshold_kg, rub in legacy_thresholds:
                if threshold_kg <= previous_kg:
                    continue
                normalized.append({"min_kg": previous_kg, "max_kg": threshold_kg, "rub": rub})
                previous_kg = threshold_kg
            if normalized:
                normalized.append({"min_kg": previous_kg, "max_kg": None, "rub": float(normalized[-1]["rub"])})
        normalized.sort(key=lambda item: (float(item.get("min_kg") or 0.0), float(item.get("max_kg") or float("inf"))))
        return normalized

    @staticmethod
    def _normalize_shipping_rules(raw_rules: Any) -> dict[str, dict[str, list[dict[str, Any]]]]:
        rules = raw_rules if isinstance(raw_rules, dict) else {}
        output: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for region in ("US", "EU", "UK"):
            region_payload = rules.get(region) if isinstance(rules.get(region), dict) else {}
            output[region] = {
                "normal": PricingSettingsService._normalize_shipping_rows(region_payload.get("normal")),
                "alt": PricingSettingsService._normalize_shipping_rows(region_payload.get("alt")),
            }
        # Fill gaps by defaults to avoid broken calculations
        for region, region_default in _DEFAULT_SHIPPING_RULES.items():
            if not output[region]["normal"]:
                output[region]["normal"] = [dict(row) for row in region_default.get("normal", [])]
            if region in {"US", "EU"} and not output[region]["alt"]:
                output[region]["alt"] = [dict(row) for row in region_default.get("alt", [])]
            if region == "UK":
                output[region]["alt"] = []
        return output

    @staticmethod
    def _normalize_image_asset_ids(raw: Any, *, limit: int) -> list[int]:
        if not isinstance(raw, list):
            return []
        result: list[int] = []
        seen: set[int] = set()
        for item in raw:
            try:
                parsed = int(item)
            except (TypeError, ValueError):
                continue
            if parsed <= 0 or parsed in seen:
                continue
            seen.add(parsed)
            result.append(parsed)
            if len(result) >= limit:
                break
        return result

    @classmethod
    def _coerce_settings_defaults(cls, entity) -> bool:
        changed = False

        if getattr(entity, "bybit_usdt_to_rub", None) is None:
            entity.bybit_usdt_to_rub = 95.0
            changed = True
        if getattr(entity, "bybit_extra_rub", None) is None:
            entity.bybit_extra_rub = 1.0
            changed = True
        normalized_rounding_mode = cls._normalize_final_rounding_mode(
            getattr(entity, "final_rounding_mode", None),
            default="unit",
        )
        if normalized_rounding_mode != getattr(entity, "final_rounding_mode", None):
            entity.final_rounding_mode = normalized_rounding_mode
            changed = True
        if not isinstance(getattr(entity, "bybit_bucket_rates", None), list):
            entity.bybit_bucket_rates = []
            changed = True
        if getattr(entity, "eur_to_usd_rate", None) is None:
            entity.eur_to_usd_rate = 1.18
            changed = True
        if getattr(entity, "gbp_to_usd_rate", None) is None:
            entity.gbp_to_usd_rate = 1.4
            changed = True
        if getattr(entity, "jpy_to_usd_rate", None) is None or float(getattr(entity, "jpy_to_usd_rate", 0.0) or 0.0) <= 0:
            entity.jpy_to_usd_rate = PricingSettingsService._fetch_jpy_to_usd_rate()
            changed = True
        if getattr(entity, "payment_fee_rate", None) is None:
            entity.payment_fee_rate = 0.02
            changed = True
        if getattr(entity, "customs_processing_rate", None) is None:
            entity.customs_processing_rate = 0.08
            changed = True
        if getattr(entity, "customs_fixed_rub", None) is None:
            entity.customs_fixed_rub = 540.0
            changed = True
        if getattr(entity, "shipping_alt_threshold_eur", None) is None:
            entity.shipping_alt_threshold_eur = 300.0
            changed = True
        if getattr(entity, "tax_rate", None) is None:
            entity.tax_rate = 0.06
            changed = True
        if getattr(entity, "dedup_only_available_products", None) is None:
            entity.dedup_only_available_products = False
            changed = True
        if getattr(entity, "show_product_description", None) is None:
            entity.show_product_description = True
            changed = True
        normalized_insurance = cls._normalize_range_rules(
            getattr(entity, "insurance_rules", None),
            min_key="min_eur",
            max_key="max_eur",
            default_rules=_DEFAULT_INSURANCE_RULES,
        )
        if normalized_insurance != (getattr(entity, "insurance_rules", None) or []):
            entity.insurance_rules = normalized_insurance
            changed = True

        normalized_fee = cls._normalize_range_rules(
            getattr(entity, "service_fee_rules", None),
            min_key="min_rub",
            max_key="max_rub",
            default_rules=_DEFAULT_SERVICE_FEE_RULES,
        )
        if normalized_fee != (getattr(entity, "service_fee_rules", None) or []):
            entity.service_fee_rules = normalized_fee
            changed = True

        raw_svc_rules = getattr(entity, "svc_rules", None)
        normalized_svc_rules = cls._normalize_svc_rules(raw_svc_rules)
        normalized_svc_rules = cls._sanitize_svc_rule_boundaries(normalized_svc_rules)
        cls._validate_svc_rules_no_overlap(normalized_svc_rules)
        if normalized_svc_rules != (raw_svc_rules or []):
            entity.svc_rules = normalized_svc_rules
            changed = True

        normalized_shipping = cls._normalize_shipping_rules(getattr(entity, "shipping_rules", None))
        if normalized_shipping != (getattr(entity, "shipping_rules", None) or {}):
            entity.shipping_rules = normalized_shipping
            changed = True
        return changed

    @staticmethod
    def _refresh_bybit_rate(entity) -> tuple[bool, str, Any | None, str | None]:
        if not app_settings.pricing_bybit_rate_auto_enabled:
            return False, "disabled", None, None
        try:
            snapshot = BybitP2PRateProvider.get_snapshot(
                fiat=app_settings.pricing_bybit_fiat,
                asset=app_settings.pricing_bybit_asset,
                timeout_sec=app_settings.pricing_bybit_rate_timeout_sec,
                cache_sec=app_settings.pricing_bybit_rate_cache_sec,
                ads_limit=app_settings.pricing_bybit_ads_limit,
                bucket_step_usdt=app_settings.pricing_bybit_bucket_step_usdt,
                bucket_max_usdt=app_settings.pricing_bybit_bucket_max_usdt,
                outlier_max_deviation_ratio=app_settings.pricing_bybit_outlier_max_deviation_ratio,
            )
            rate = float(snapshot.representative_rate)
        except Exception as exc:
            error_text = str(exc) or "Bybit fetch failed"
            if getattr(entity, "bybit_last_error", None) != error_text:
                entity.bybit_last_error = error_text[:1024]
                return True, "fallback_stored", None, error_text
            return False, "fallback_stored", None, error_text
        if rate <= 0:
            error_text = "Bybit returned non-positive rate"
            if getattr(entity, "bybit_last_error", None) != error_text:
                entity.bybit_last_error = error_text
                return True, "fallback_stored", None, error_text
            return False, "fallback_stored", None, error_text
        bucket_rates = PricingSettingsService._normalize_bybit_bucket_rates(
            getattr(snapshot, "bucket_quotes", None),
            step_usdt=int(app_settings.pricing_bybit_bucket_step_usdt),
            max_usdt=int(app_settings.pricing_bybit_bucket_max_usdt),
        )
        current = float(getattr(entity, "bybit_usdt_to_rub", 0.0) or 0.0)
        changed = False
        if abs(current - rate) > 1e-6:
            entity.bybit_usdt_to_rub = float(rate)
            changed = True
        if (getattr(entity, "bybit_bucket_rates", None) or []) != bucket_rates:
            entity.bybit_bucket_rates = bucket_rates
            changed = True
        if getattr(entity, "bybit_last_error", None):
            entity.bybit_last_error = None
            changed = True
        snapshot_epoch = PricingSettingsService._safe_float(getattr(snapshot, "fetched_at", None))
        if snapshot_epoch is not None and snapshot_epoch > 0:
            try:
                refreshed_at = datetime.fromtimestamp(float(snapshot_epoch), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                refreshed_at = datetime.now(timezone.utc)
        else:
            refreshed_at = datetime.now(timezone.utc)
        current_updated_at = getattr(entity, "bybit_last_updated_at", None)
        if current_updated_at is None or abs((refreshed_at - current_updated_at).total_seconds()) > 0.5:
            entity.bybit_last_updated_at = refreshed_at
            changed = True
        if not changed:
            return False, "live_cached", snapshot, None
        return True, "live_updated", snapshot, None

    def get_settings(self, *, refresh_bybit: bool = True) -> PricingSettingsResponse:
        bootstrap_changed = self._bootstrap_suppliers()
        entity, created = self.repo.get_or_create_default()
        defaults_changed = self._coerce_settings_defaults(entity)
        bybit_status = "skipped"
        bybit_warning = None
        bybit_snapshot = None
        bybit_error = None
        if refresh_bybit:
            bybit_changed, bybit_status, bybit_snapshot, bybit_error = self._refresh_bybit_rate(entity)
            if bybit_status == "fallback_stored":
                bybit_warning = "WARN: Bybit временно недоступен, используется сохраненный курс."
                if bybit_error:
                    bybit_warning = f"{bybit_warning} Причина: {bybit_error}"
        else:
            bybit_changed = False
            bybit_snapshot = None
            bybit_error = str(getattr(entity, "bybit_last_error", "") or "") or None
            if bybit_error:
                bybit_warning = f"WARN: Bybit временно недоступен, используется сохраненный курс. Причина: {bybit_error}"
        if created or bootstrap_changed or defaults_changed or bybit_changed:
            self.db.commit()
            self.db.refresh(entity)
        suppliers = self.supplier_repo.list_all_with_rates()
        return self._to_response(
            entity,
            suppliers=suppliers,
            bybit_rate_status=bybit_status,
            bybit_rate_warning=bybit_warning,
            bybit_snapshot=bybit_snapshot,
            bybit_last_error=bybit_error,
        )

    def update_settings(self, payload: PricingSettingsUpdateRequest) -> PricingSettingsResponse:
        bootstrap_changed = self._bootstrap_suppliers()
        entity, created = self.repo.get_or_create_default()
        patch = payload.model_dump(exclude_none=True)
        for forbidden_key in ("usd_to_rub", "eur_to_rub", "bybit_usdt_to_rub"):
            patch.pop(forbidden_key, None)
        if "customs_threshold_currency" in patch:
            patch["customs_threshold_currency"] = self._normalize_currency(
                patch.get("customs_threshold_currency"),
                default=self._normalize_currency(
                    getattr(entity, "customs_threshold_currency", None),
                    default="EUR",
                    allowed={"EUR", "GBP"},
                ),
                allowed={"EUR", "GBP"},
            )
        if "final_rounding_mode" in patch:
            patch["final_rounding_mode"] = self._normalize_final_rounding_mode(
                patch.get("final_rounding_mode"),
                default=self._normalize_final_rounding_mode(getattr(entity, "final_rounding_mode", None), default="unit"),
            )
        if "insurance_rules" in patch:
            patch["insurance_rules"] = self._normalize_range_rules(
                patch.get("insurance_rules"),
                min_key="min_eur",
                max_key="max_eur",
                default_rules=_DEFAULT_INSURANCE_RULES,
            )
        if "service_fee_rules" in patch:
            patch["service_fee_rules"] = self._normalize_range_rules(
                patch.get("service_fee_rules"),
                min_key="min_rub",
                max_key="max_rub",
                default_rules=_DEFAULT_SERVICE_FEE_RULES,
            )
        if "svc_rules" in patch:
            patch["svc_rules"] = self._normalize_svc_rules(patch.get("svc_rules"))
            self._validate_svc_rules_no_overlap(patch["svc_rules"])
        if "shipping_rules" in patch:
            patch["shipping_rules"] = self._normalize_shipping_rules(patch.get("shipping_rules"))
        if "dedup_only_available_products" in patch:
            patch["dedup_only_available_products"] = bool(patch.get("dedup_only_available_products"))
        if "show_product_description" in patch:
            patch["show_product_description"] = bool(patch.get("show_product_description"))
        for key, value in patch.items():
            setattr(entity, key, value)
        defaults_changed = self._coerce_settings_defaults(entity)
        self.db.commit()
        if created or patch or defaults_changed or bootstrap_changed:
            self.db.refresh(entity)
        suppliers = self.supplier_repo.list_all_with_rates()
        return self._to_response(entity, suppliers=suppliers)

    @staticmethod
    def _to_response(
        entity,
        *,
        suppliers: list[ParserSupplier],
        bybit_rate_status: str = "unknown",
        bybit_rate_warning: str | None = None,
        bybit_snapshot: Any | None = None,
        bybit_last_error: str | None = None,
    ) -> PricingSettingsResponse:
        normalized_insurance = PricingSettingsService._normalize_range_rules(
            getattr(entity, "insurance_rules", None),
            min_key="min_eur",
            max_key="max_eur",
            default_rules=_DEFAULT_INSURANCE_RULES,
        )
        normalized_service_fee = PricingSettingsService._normalize_range_rules(
            getattr(entity, "service_fee_rules", None),
            min_key="min_rub",
            max_key="max_rub",
            default_rules=_DEFAULT_SERVICE_FEE_RULES,
        )
        normalized_svc_rules = PricingSettingsService._normalize_svc_rules(getattr(entity, "svc_rules", None))
        PricingSettingsService._validate_svc_rules_no_overlap(normalized_svc_rules)
        normalized_shipping = PricingSettingsService._normalize_shipping_rules(getattr(entity, "shipping_rules", None))
        bybit_bucket_rates = PricingSettingsService._normalize_bybit_bucket_rates(
            getattr(entity, "bybit_bucket_rates", None),
            step_usdt=int(app_settings.pricing_bybit_bucket_step_usdt),
            max_usdt=int(app_settings.pricing_bybit_bucket_max_usdt),
        )
        if not bybit_bucket_rates:
            bybit_bucket_rates = PricingSettingsService._normalize_bybit_bucket_rates(
                getattr(bybit_snapshot, "bucket_quotes", None),
                step_usdt=int(app_settings.pricing_bybit_bucket_step_usdt),
                max_usdt=int(app_settings.pricing_bybit_bucket_max_usdt),
            )
        entity_last_updated = getattr(entity, "bybit_last_updated_at", None)
        bybit_last_updated_at = entity_last_updated.isoformat() if entity_last_updated else None
        if bybit_last_updated_at is None:
            bybit_last_updated_at = PricingSettingsService._format_epoch_iso(
                PricingSettingsService._safe_float(getattr(bybit_snapshot, "fetched_at", None))
            )
        bybit_last_error_value = bybit_last_error or str(getattr(entity, "bybit_last_error", "") or "") or None
        if bybit_last_error_value is None and bybit_rate_status == "fallback_stored":
            bybit_last_error_value = "Bybit fetch failed"
        effective_usd_to_rub, effective_eur_to_rub = PricingSettingsService._effective_rates_from_entity(entity)
        return PricingSettingsResponse(
            markup_multiplier=float(entity.markup_multiplier),
            weight_tolerance=float(entity.weight_tolerance),
            promo_factor=float(entity.promo_factor),
            customs_threshold_eur=float(entity.customs_threshold_eur),
            customs_threshold_currency=PricingSettingsService._normalize_currency(
                getattr(entity, "customs_threshold_currency", None),
                default="EUR",
                allowed={"EUR", "GBP"},
            ),
            customs_duty_rate=float(entity.customs_duty_rate),
            bybit_usdt_to_rub=float(getattr(entity, "bybit_usdt_to_rub", 95.0) or 95.0),
            bybit_extra_rub=float(getattr(entity, "bybit_extra_rub", 1.0)),
            eur_to_usd_rate=float(getattr(entity, "eur_to_usd_rate", 1.18)),
            gbp_to_usd_rate=float(getattr(entity, "gbp_to_usd_rate", 1.4)),
            jpy_to_usd_rate=float(getattr(entity, "jpy_to_usd_rate")),
            final_rounding_mode=PricingSettingsService._normalize_final_rounding_mode(
                getattr(entity, "final_rounding_mode", None),
                default="unit",
            ),
            payment_fee_rate=float(getattr(entity, "payment_fee_rate", 0.02)),
            customs_processing_rate=float(getattr(entity, "customs_processing_rate", 0.08)),
            customs_fixed_rub=float(getattr(entity, "customs_fixed_rub", 540.0)),
            shipping_alt_threshold_eur=float(getattr(entity, "shipping_alt_threshold_eur", 300.0)),
            tax_rate=float(getattr(entity, "tax_rate", 0.06)),
            dedup_only_available_products=bool(getattr(entity, "dedup_only_available_products", False)),
            show_product_description=bool(getattr(entity, "show_product_description", True)),
            svc_rules=normalized_svc_rules,
            insurance_rules=normalized_insurance,
            service_fee_rules=normalized_service_fee,
            shipping_rules=normalized_shipping,
            bybit_rate_status=bybit_rate_status,
            bybit_rate_warning=bybit_rate_warning,
            bybit_bucket_step_usdt=int(app_settings.pricing_bybit_bucket_step_usdt),
            bybit_bucket_max_usdt=int(app_settings.pricing_bybit_bucket_max_usdt),
            bybit_bucket_rates=bybit_bucket_rates,
            bybit_worker_auto_enabled=bool(app_settings.pricing_bybit_rate_auto_enabled),
            bybit_worker_interval_sec=int(app_settings.pricing_bybit_worker_interval_sec),
            bybit_last_updated_at=bybit_last_updated_at,
            bybit_last_error=bybit_last_error_value,
            suppliers=[
                PricingSettingsService._supplier_to_response(
                    item,
                    usd_to_rub=effective_usd_to_rub,
                    eur_to_rub=effective_eur_to_rub,
                )
                for item in suppliers
            ],
            formula_latex=_FORMULA_LATEX,
            formula_lines=list(_FORMULA_LINES),
            formula_legend=[dict(item) for item in _FORMULA_LEGEND],
        )

    def get_admin_ui_settings(self) -> AdminUiSettingsResponse:
        entity = self.db.query(AdminUiSettings).filter(AdminUiSettings.id == 1).one_or_none()
        if entity is None:
            entity = AdminUiSettings(id=1)
            self.db.add(entity)
            self.db.commit()
            self.db.refresh(entity)
        return AdminUiSettingsResponse(
            designers_min_products=max(1, int(getattr(entity, "designers_min_products", 1) or 1)),
            designers_exclude_store_vendors=bool(getattr(entity, "designers_exclude_store_vendors", False)),
            showcase_hero_image_asset_id=(
                int(getattr(entity, "showcase_hero_image_asset_id"))
                if isinstance(getattr(entity, "showcase_hero_image_asset_id", None), int)
                and int(getattr(entity, "showcase_hero_image_asset_id")) > 0
                else None
            ),
            showcase_carousel_image_asset_ids=self._normalize_image_asset_ids(
                getattr(entity, "showcase_carousel_image_asset_ids", None),
                limit=20,
            ),
        )

    def update_admin_ui_settings(self, payload: AdminUiSettingsUpdateRequest) -> AdminUiSettingsResponse:
        patch = payload.model_dump(exclude_unset=True)
        if "designers_min_products" in patch:
            patch["designers_min_products"] = max(1, int(patch.get("designers_min_products") or 1))
        if "designers_exclude_store_vendors" in patch:
            patch["designers_exclude_store_vendors"] = bool(patch.get("designers_exclude_store_vendors"))
        if "showcase_hero_image_asset_id" in patch:
            raw_hero = patch.get("showcase_hero_image_asset_id")
            patch["showcase_hero_image_asset_id"] = int(raw_hero) if isinstance(raw_hero, int) and raw_hero > 0 else None
        if "showcase_carousel_image_asset_ids" in patch:
            patch["showcase_carousel_image_asset_ids"] = self._normalize_image_asset_ids(
                patch.get("showcase_carousel_image_asset_ids"),
                limit=20,
            )
        entity = self.db.query(AdminUiSettings).filter(AdminUiSettings.id == 1).one_or_none()
        if entity is None:
            entity = AdminUiSettings(id=1)
        for key, value in patch.items():
            setattr(entity, key, value)
        self.db.add(entity)
        self.db.commit()
        self.db.refresh(entity)
        return self.get_admin_ui_settings()

    @staticmethod
    def _supplier_to_response(
        supplier: ParserSupplier,
        *,
        usd_to_rub: float,
        eur_to_rub: float,
    ) -> PricingSupplierResponse:
        rate_currency = PricingSettingsService._normalize_currency(getattr(supplier, "rate_currency", None), default="RUB")
        rates = PricingSettingsService._normalize_shipping_rows(
            [
                {
                    "min_kg": getattr(item, "min_kg", 0.0),
                    "max_kg": getattr(item, "max_kg", None),
                    "rub": getattr(item, "rate_rub", 0.0),
                }
                for item in sorted(
                    supplier.shipping_rates,
                    key=lambda row: (float(getattr(row, "min_kg", 0.0) or 0.0), float(getattr(row, "max_kg", float("inf")) or float("inf"))),
                )
            ]
        )
        return PricingSupplierResponse(
            id=int(supplier.id),
            key=supplier.key,
            name=supplier.name,
            category=PricingSettingsService._normalize_supplier_category(getattr(supplier, "category", None)),
            parent_supplier_id=(
                int(getattr(supplier, "parent_supplier_id", 0))
                if getattr(supplier, "parent_supplier_id", None) is not None
                else None
            ),
            alt_position=max(0, int(getattr(supplier, "alt_position", 0) or 0)),
            rate_currency=rate_currency,
            rates=[
                {
                    "min_kg": float(item["min_kg"]),
                    "max_kg": (float(item["max_kg"]) if item.get("max_kg") is not None else None),
                    "rub": float(item["rub"]),
                }
                for item in rates
            ],
        )

    @staticmethod
    def _default_tariff_ranges(*, region: str, mode: str) -> list[dict[str, Any]]:
        defaults = _DEFAULT_SHIPPING_RULES.get(region, {}).get(mode, [])
        return [
            {
                "min_kg": float(item["min_kg"]),
                "max_kg": (float(item["max_kg"]) if item.get("max_kg") is not None else None),
                "rub": float(item["rub"]),
            }
            for item in defaults
        ]

    def update_supplier(self, supplier_id: int, payload: PricingSupplierUpdateRequest) -> PricingSupplierResponse:
        self._bootstrap_suppliers()
        supplier = self.supplier_repo.get_by_id(supplier_id)
        if supplier is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")

        patch = payload.model_dump(exclude_none=True)
        settings_entity, _ = self.repo.get_or_create_default()
        usd_to_rub_effective, eur_to_rub_effective = self._effective_rates_from_entity(settings_entity)
        for key in ("name",):
            if key in patch:
                setattr(supplier, key, patch[key])
        if "category" in patch and getattr(supplier, "parent_supplier_id", None) is None:
            supplier.category = self._normalize_supplier_category(patch.get("category"), default=supplier.category)
        if "alt_position" in patch:
            supplier.alt_position = max(0, int(patch.get("alt_position") or 0))
        if "rate_currency" in patch:
            supplier.rate_currency = self._normalize_currency(patch.get("rate_currency"), default=supplier.rate_currency)
        if "rates" in patch:
            normalized = self._normalize_shipping_rows(patch.get("rates"))
            self.supplier_repo.replace_ranges(supplier_id=int(supplier.id), ranges=normalized)

        self.db.commit()
        refreshed = self.supplier_repo.get_by_id(supplier.id)
        if refreshed is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден после обновления")
        self.db.refresh(refreshed)
        return self._supplier_to_response(
            refreshed,
            usd_to_rub=usd_to_rub_effective,
            eur_to_rub=eur_to_rub_effective,
        )

    def create_supplier(self, payload: PricingSupplierCreateRequest) -> PricingSupplierResponse:
        self._bootstrap_suppliers()

        base_key = (payload.key or "").strip().lower()
        if not base_key:
            base_key = re.sub(r"[^a-z0-9]+", "-", payload.name.lower()).strip("-")
        if not base_key:
            base_key = "supplier"

        key = base_key
        suffix = 2
        while self.supplier_repo.get_by_key(key) is not None:
            key = f"{base_key}-{suffix}"
            suffix += 1

        parent_supplier_id = payload.parent_supplier_id
        parent_supplier = None
        if parent_supplier_id is not None:
            parent_supplier = self._validate_alt_parent(self.supplier_repo.get_by_id(int(parent_supplier_id)))
            self._validate_alt_limit(int(parent_supplier.id))

        self.db.execute(
            text(
                """
                SELECT setval(
                    pg_get_serial_sequence('parser_supplier', 'id'),
                    COALESCE((SELECT MAX(id) FROM parser_supplier), 1),
                    true
                )
                """
            )
        )
        supplier = self.supplier_repo.create(
            key=key,
            name=payload.name.strip(),
            category=(
                "alt"
                if parent_supplier is not None
                else self._normalize_supplier_category(payload.category, default="main")
            ),
            parent_supplier_id=(int(parent_supplier.id) if parent_supplier is not None else None),
            alt_position=max(0, int(payload.alt_position or 0)),
            rate_currency=self._normalize_currency(payload.rate_currency, default="RUB"),
        )
        self.supplier_repo.flush()
        settings_entity, _ = self.repo.get_or_create_default()
        usd_to_rub_effective, eur_to_rub_effective = self._effective_rates_from_entity(settings_entity)
        incoming_rates = self._normalize_shipping_rows(payload.rates or [])
        if not incoming_rates:
            region = self._infer_shipping_region(supplier.name, supplier.key)
            mode = "alt" if supplier.parent_supplier_id is not None else "normal"
            incoming_rates = self._default_tariff_ranges(region=region, mode=mode)
        self.supplier_repo.replace_ranges(supplier_id=int(supplier.id), ranges=incoming_rates)
        self.db.commit()
        created = self.supplier_repo.get_by_id(int(supplier.id))
        if created is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось создать тариф")
        return self._supplier_to_response(
            created,
            usd_to_rub=usd_to_rub_effective,
            eur_to_rub=eur_to_rub_effective,
        )

    def delete_supplier(self, supplier_id: int) -> dict[str, str]:
        supplier = self.supplier_repo.get_by_id(supplier_id)
        if supplier is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
        assigned_sources = self.source_repo.count_by_supplier_id(supplier.id)
        if assigned_sources > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Нельзя удалить: тариф назначен на {assigned_sources} источников",
            )
        if getattr(supplier, "parent_supplier_id", None) is None:
            alt_count = (
                self.supplier_repo.query()
                .filter(ParserSupplier.parent_supplier_id == int(supplier.id))
                .count()
            )
            if alt_count > 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Нельзя удалить: у тарифа есть {alt_count} альтернатив",
                )
        try:
            self.db.delete(supplier)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Нельзя удалить тариф: есть связанные записи")
        return {"status": "ok", "message": "Тариф удален"}

    @staticmethod
    def _resolve_supplier_rate(
        *,
        supplier_id: int | None,
        billable_kg: float,
        use_alt_rate: bool,
        settings: PricingSettingsResponse,
    ) -> tuple[float, dict[str, Any]]:
        suppliers = settings.suppliers or []
        selected = next((item for item in suppliers if supplier_id is not None and item.id == supplier_id), None)
        if selected is None:
            return 0.0, {
                "supplier_id": supplier_id,
                "supplier_key": None,
                "supplier_name": None,
                "shipping_region": "EU",
                "shipping_mode": "normal",
                "shipping_billable_kg": round(float(billable_kg), 4),
                "shipping_rate_mode": "missing_supplier",
            }

        mode = "alt" if use_alt_rate else "normal"
        selected_candidate = selected
        candidates = [selected]
        if use_alt_rate and getattr(selected, "parent_supplier_id", None) is None:
            alternatives = [
                item
                for item in suppliers
                if getattr(item, "parent_supplier_id", None) == int(selected.id)
            ]
            alternatives.sort(key=lambda item: (int(getattr(item, "alt_position", 0) or 0), int(item.id)))
            candidates = alternatives if alternatives else [selected]

        selected_candidate = candidates[0] if candidates else selected
        region = PricingSettingsService._infer_shipping_region(selected_candidate.name, selected_candidate.key)
        tariff_rows = selected_candidate.rates or []
        normalized_tariff_rows = PricingSettingsService._normalize_shipping_rows(tariff_rows)
        if normalized_tariff_rows:
            value, meta = PricingSettingsService._resolve_tariff_by_weight(
                rows=normalized_tariff_rows,
                billable_kg=billable_kg,
            )
        else:
            shipping_tree = settings.shipping_rules or {}
            region_tree = shipping_tree.get(region) if isinstance(shipping_tree, dict) else None
            mode_rows: list[dict[str, Any]] = []
            if isinstance(region_tree, dict):
                raw_rows = region_tree.get(mode) or []
                if isinstance(raw_rows, list):
                    mode_rows = [row for row in raw_rows if isinstance(row, dict)]
            value, meta = PricingSettingsService._resolve_tariff_by_weight(
                rows=mode_rows,
                billable_kg=billable_kg,
            )

        return float(value or 0.0), {
            "supplier_id": selected_candidate.id,
            "supplier_key": selected_candidate.key,
            "supplier_name": selected_candidate.name,
            "shipping_region": region,
            "shipping_mode": mode,
            "shipping_billable_kg": round(float(billable_kg), 4),
            **meta,
        }

    @staticmethod
    def _infer_shipping_region(supplier_name: str | None, supplier_key: str | None) -> str:
        name_value = (supplier_name or "").strip().upper()
        key_value = (supplier_key or "").strip().lower()
        if name_value == "US" or key_value.startswith("us-"):
            return "US"
        if name_value in {"UK", "GB"} or key_value.startswith("uk-") or key_value.startswith("gb-"):
            return "UK"
        return "EU"

    @staticmethod
    def _resolve_tariff_by_weight(*, rows: list[dict[str, Any]], billable_kg: float) -> tuple[float, dict[str, Any]]:
        normalized_rows = PricingSettingsService._normalize_shipping_rows(rows)
        if not normalized_rows:
            return 0.0, {
                "shipping_rate_mode": "missing_tariff",
                "shipping_tariff_min_kg": None,
                "shipping_tariff_max_kg": None,
                "shipping_tariff_label": None,
            }

        target_kg = max(0.5, float(billable_kg))
        for row in normalized_rows:
            min_kg = max(0.0, float(row.get("min_kg") or 0.0))
            max_kg_raw = row.get("max_kg")
            max_kg = None if max_kg_raw is None else max(0.0, float(max_kg_raw))
            rub = max(0.0, float(row.get("rub") or 0.0))
            min_ok = target_kg >= (min_kg - 1e-9)
            max_ok = True if max_kg is None else target_kg <= (max_kg + 1e-9)
            if min_ok and max_ok:
                label = f"{min_kg:g}+ кг" if max_kg is None else f"{min_kg:g}-{max_kg:g} кг"
                return rub, {
                    "shipping_rate_mode": "range_match",
                    "shipping_tariff_min_kg": min_kg,
                    "shipping_tariff_max_kg": max_kg,
                    "shipping_tariff_label": label,
                    "shipping_tariff_rub": rub,
                }

        last = normalized_rows[-1]
        last_min = max(0.0, float(last.get("min_kg") or 0.0))
        last_max_raw = last.get("max_kg")
        last_max = None if last_max_raw is None else max(0.0, float(last_max_raw))
        last_rub = max(0.0, float(last.get("rub") or 0.0))
        last_label = f"{last_min:g}+ кг" if last_max is None else f"{last_min:g}-{last_max:g} кг"
        return last_rub, {
            "shipping_rate_mode": "range_clamped_last",
            "shipping_tariff_min_kg": last_min,
            "shipping_tariff_max_kg": last_max,
            "shipping_tariff_label": last_label,
            "shipping_tariff_rub": last_rub,
        }

    @staticmethod
    def _pick_range_rule(
        *,
        value: float,
        rules: list[dict[str, Any]],
        min_key: str,
        max_key: str,
    ) -> dict[str, Any] | None:
        target = float(value)
        for row in rules:
            if not isinstance(row, dict):
                continue
            min_raw = PricingSettingsService._safe_float(row.get(min_key))
            max_raw = PricingSettingsService._safe_float(row.get(max_key))
            min_ok = True if min_raw is None else target >= float(min_raw)
            max_ok = True if max_raw is None else target <= float(max_raw)
            if min_ok and max_ok:
                return row
        return None

    @staticmethod
    def _compute_rule_amount(base_value: float, rule: dict[str, Any] | None) -> tuple[float, dict[str, Any]]:
        if not isinstance(rule, dict):
            return 0.0, {"mode": "missing_rule", "value": 0.0}
        mode = PricingSettingsService._normalize_rule_mode(str(rule.get("mode") or "fixed_rub"))
        value = max(0.0, float(PricingSettingsService._safe_float(rule.get("value")) or 0.0))
        if mode == "percent":
            amount = float(base_value) * value
        else:
            amount = value
        return max(0.0, float(amount)), {"mode": mode, "value": value}

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip().replace(",", ".")
            if not stripped:
                return None
            try:
                return float(stripped)
            except ValueError:
                return None
        return None

    @staticmethod
    def _format_epoch_iso(epoch_sec: float | None) -> str | None:
        if epoch_sec is None or epoch_sec <= 0:
            return None
        try:
            dt = datetime.fromtimestamp(float(epoch_sec), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None
        return dt.isoformat()

    @staticmethod
    def _normalize_bybit_bucket_rates(
        raw_quotes: Any,
        *,
        step_usdt: int,
        max_usdt: int,
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_quotes, list):
            return []
        normalized: list[dict[str, Any]] = []
        for quote in raw_quotes:
            if isinstance(quote, dict):
                bucket_raw = quote.get("bucket_usdt")
                rate_raw = quote.get("rate_rub_per_usdt")
                pay_raw = quote.get("pay_rub")
                source_raw = quote.get("source")
                order_raw = quote.get("order_id")
                nick_raw = quote.get("nickname")
                min_raw = quote.get("min_rub")
                max_raw = quote.get("max_rub")
                qty_raw = quote.get("quantity_usdt")
                legs_raw = quote.get("legs")
            else:
                bucket_raw = getattr(quote, "bucket_usdt", None)
                rate_raw = getattr(quote, "rate_rub_per_usdt", None)
                pay_raw = getattr(quote, "pay_rub", None)
                source_raw = getattr(quote, "source", None)
                order_raw = getattr(quote, "order_id", None)
                nick_raw = getattr(quote, "nickname", None)
                min_raw = getattr(quote, "min_rub", None)
                max_raw = getattr(quote, "max_rub", None)
                qty_raw = getattr(quote, "quantity_usdt", None)
                legs_raw = getattr(quote, "legs", None)
            bucket_usdt = PricingSettingsService._safe_float(bucket_raw)
            rate_rub_per_usdt = PricingSettingsService._safe_float(rate_raw)
            pay_rub = PricingSettingsService._safe_float(pay_raw)
            if bucket_usdt is None or bucket_usdt <= 0:
                continue
            if rate_rub_per_usdt is None or rate_rub_per_usdt <= 0:
                continue
            if pay_rub is None or pay_rub <= 0:
                pay_rub = bucket_usdt * rate_rub_per_usdt
            normalized.append(
                {
                    "bucket_usdt": round(float(bucket_usdt), 4),
                    "rate_rub_per_usdt": round(float(rate_rub_per_usdt), 6),
                    "pay_rub": round(float(pay_rub), 2),
                    "source": str(source_raw or "single_ad"),
                    "order_id": str(order_raw or "") or None,
                    "nickname": str(nick_raw or "") or None,
                    "min_rub": PricingSettingsService._safe_float(min_raw),
                    "max_rub": PricingSettingsService._safe_float(max_raw),
                    "quantity_usdt": PricingSettingsService._safe_float(qty_raw),
                    "legs": legs_raw if isinstance(legs_raw, list) else [],
                }
            )
        normalized.sort(key=lambda item: float(item.get("bucket_usdt") or 0.0))
        if step_usdt <= 0 or max_usdt <= 0:
            return normalized
        # Clamp oversized ranges from stale caches with different env.
        output = [
            row
            for row in normalized
            if float(row.get("bucket_usdt") or 0.0) <= float(max_usdt)
            and float(row.get("bucket_usdt") or 0.0) % float(step_usdt) <= 1e-6
        ]
        return output

    @staticmethod
    def _pick_bybit_bucket_quote(
        *,
        target_usdt: float,
        settings: PricingSettingsResponse,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        safe_target_usdt = max(1.0, float(target_usdt))
        rows: list[tuple[float, dict[str, Any]]] = []
        for row in settings.bybit_bucket_rates or []:
            if not isinstance(row, dict):
                continue
            bucket_usdt = PricingSettingsService._safe_float(row.get("bucket_usdt"))
            rate = PricingSettingsService._safe_float(row.get("rate_rub_per_usdt"))
            if bucket_usdt is None or bucket_usdt <= 0:
                continue
            if rate is None or rate <= 0:
                continue
            rows.append((float(bucket_usdt), row))
        rows.sort(key=lambda item: item[0])
        if not rows:
            return None, {
                "bybit_bucket_target_usdt": round(safe_target_usdt, 4),
                "bybit_bucket_selected_usdt": None,
                "bybit_bucket_strategy": "flat_rate",
                "bybit_bucket_source": "flat_rate",
                "bybit_bucket_order_id": None,
                "bybit_bucket_order_nickname": None,
                "bybit_bucket_pay_rub": None,
                "bybit_bucket_legs_count": 0,
            }

        selected_row: dict[str, Any] | None = None
        selected_bucket_usdt: float | None = None
        strategy = "nearest_up"
        for bucket_usdt, row in rows:
            if bucket_usdt + 1e-9 >= safe_target_usdt:
                selected_row = row
                selected_bucket_usdt = bucket_usdt
                break
        if selected_row is None:
            selected_bucket_usdt, selected_row = rows[-1]
            strategy = "max_available"

        return selected_row, {
            "bybit_bucket_target_usdt": round(safe_target_usdt, 4),
            "bybit_bucket_selected_usdt": round(float(selected_bucket_usdt or 0.0), 4) if selected_bucket_usdt else None,
            "bybit_bucket_strategy": strategy,
            "bybit_bucket_source": str(selected_row.get("source") or "single_ad"),
            "bybit_bucket_order_id": selected_row.get("order_id"),
            "bybit_bucket_order_nickname": selected_row.get("nickname"),
            "bybit_bucket_pay_rub": selected_row.get("pay_rub"),
            "bybit_bucket_legs_count": len(selected_row.get("legs") or []),
        }

    @staticmethod
    def _pick_bybit_bucket_rate(
        *,
        target_usdt: float,
        settings: PricingSettingsResponse,
    ) -> tuple[float, dict[str, Any]]:
        base_rate = max(0.0, float(settings.bybit_usdt_to_rub))
        return base_rate, {
            "bybit_bucket_target_usdt": round(max(1.0, float(target_usdt)), 4),
            "bybit_bucket_selected_usdt": None,
            "bybit_bucket_strategy": "single_rate_all_products",
            "bybit_bucket_source": "first_adequate_order",
            "bybit_bucket_order_id": None,
            "bybit_bucket_order_nickname": None,
            "bybit_bucket_pay_rub": None,
            "bybit_bucket_legs_count": 0,
        }

    @staticmethod
    def _has_discount_in_variants(variants: list[dict[str, Any]] | None) -> bool:
        if not variants:
            return False
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            price = PricingSettingsService._safe_float(variant.get("price"))
            compare_at = PricingSettingsService._safe_float(variant.get("compare_at_price"))
            if price is not None and compare_at is not None and compare_at > price:
                return True
        return False

    @staticmethod
    def calculate_for_product(
        *,
        source_price: float | None,
        source_currency: str | None,
        weight_grams: float | None,
        supplier_id: int | None,
        promo_factor: float | None,
        promo_only_no_discount: bool | None,
        buyout_surcharge_value: float | None,
        buyout_surcharge_currency: str | None,
        variants: list[dict[str, Any]] | None,
        settings: PricingSettingsResponse,
    ) -> ProductPricingComputation:
        currency = (source_currency or "").upper()
        if source_price is None:
            return ProductPricingComputation(None, True, "missing_source_price", {"source_currency": currency or None})
        if weight_grams is None or weight_grams <= 0:
            return ProductPricingComputation(
                None,
                True,
                "missing_weight",
                {"source_price": float(source_price), "source_currency": currency or None},
            )

        base_usdt_to_rub = max(0.0, float(settings.bybit_usdt_to_rub))
        bybit_extra_rub = max(0.0, float(settings.bybit_extra_rub))
        eur_to_usd_rate = max(0.0001, float(settings.eur_to_usd_rate))
        gbp_to_usd_rate = max(0.0001, float(settings.gbp_to_usd_rate))
        jpy_to_usd_rate = max(0.000001, float(settings.jpy_to_usd_rate))
        if base_usdt_to_rub <= 0:
            return ProductPricingComputation(
                None,
                True,
                "invalid_fx_settings",
                {"source_price": float(source_price), "source_currency": currency or None},
            )

        if currency == "USD":
            sp_usd = float(source_price)
        elif currency == "EUR":
            sp_usd = float(source_price) * eur_to_usd_rate
        elif currency == "GBP":
            sp_usd = float(source_price) * gbp_to_usd_rate
        elif currency == "JPY":
            sp_usd = float(source_price) * jpy_to_usd_rate
        elif currency == "RUB":
            sp_usd = float(source_price) / base_usdt_to_rub
        else:
            return ProductPricingComputation(
                None,
                True,
                "unsupported_currency",
                {"source_price": float(source_price), "source_currency": currency or None},
            )

        bybit_bucket_rate, bybit_bucket_meta = PricingSettingsService._pick_bybit_bucket_rate(
            target_usdt=max(1.0, sp_usd),
            settings=settings,
        )
        if bybit_bucket_rate <= 0:
            bybit_bucket_rate = base_usdt_to_rub
        effective_usdt_to_rub = bybit_bucket_rate + bybit_extra_rub
        eur_to_rub_effective = eur_to_usd_rate * effective_usdt_to_rub
        if effective_usdt_to_rub <= 0 or eur_to_rub_effective <= 0:
            return ProductPricingComputation(
                None,
                True,
                "invalid_fx_settings",
                {"source_price": float(source_price), "source_currency": currency or None},
            )
        if currency == "RUB":
            sp_usd = float(source_price) / effective_usdt_to_rub

        sp_rub = sp_usd * effective_usdt_to_rub
        sp_eur = sp_usd / eur_to_usd_rate

        effective_weight_grams = max(1.0, float(weight_grams) * settings.weight_tolerance)
        billable_kg = max(0.001, effective_weight_grams / 1000.0)
        effective_buyout_surcharge_value = max(0.0, float(buyout_surcharge_value or 0.0))
        effective_buyout_surcharge_currency = PricingSettingsService._normalize_currency(
            buyout_surcharge_currency,
            default=currency if currency in {"RUB", "USD", "EUR", "GBP", "JPY"} else "RUB",
            allowed={"RUB", "USD", "EUR", "GBP", "JPY"},
        )
        if effective_buyout_surcharge_currency == "GBP":
            buyout_surcharge_rub = effective_buyout_surcharge_value * gbp_to_usd_rate * effective_usdt_to_rub
        elif effective_buyout_surcharge_currency == "JPY":
            buyout_surcharge_rub = effective_buyout_surcharge_value * jpy_to_usd_rate * effective_usdt_to_rub
        else:
            buyout_surcharge_rub = PricingSettingsService._to_rub(
                effective_buyout_surcharge_value,
                effective_buyout_surcharge_currency,
                usd_to_rub=effective_usdt_to_rub,
                eur_to_rub=eur_to_rub_effective,
            )
        buyout_surcharge_eur = buyout_surcharge_rub / eur_to_rub_effective if eur_to_rub_effective > 0 else 0.0
        effective_promo_factor = max(0.0, float(settings.promo_factor if promo_factor is None else promo_factor))
        promo_only_no_discount_enabled = bool(promo_only_no_discount)
        has_source_discount = PricingSettingsService._has_discount_in_variants(variants)
        promo_applied_factor = 1.0 if promo_only_no_discount_enabled and has_source_discount else effective_promo_factor

        sp_after_promo_rub = sp_rub * promo_applied_factor
        sp_after_promo_eur = sp_after_promo_rub / eur_to_rub_effective if eur_to_rub_effective > 0 else sp_eur
        buyout_rub = sp_after_promo_rub + buyout_surcharge_rub

        payment_fee_rub = buyout_rub * max(0.0, float(settings.payment_fee_rate))

        insurance_rule = PricingSettingsService._pick_range_rule(
            value=sp_after_promo_eur,
            rules=settings.insurance_rules,
            min_key="min_eur",
            max_key="max_eur",
        )
        insurance_rub, insurance_meta = PricingSettingsService._compute_rule_amount(sp_after_promo_rub, insurance_rule)

        customs_excess_eur = max(0.0, sp_after_promo_eur - float(settings.customs_threshold_eur))
        customs_duty_eur = customs_excess_eur * max(0.0, float(settings.customs_duty_rate))
        customs_processing_eur = customs_duty_eur * max(0.0, float(settings.customs_processing_rate))
        customs_base_rub = (customs_duty_eur + customs_processing_eur) * eur_to_rub_effective
        customs_fixed_rub = max(0.0, float(settings.customs_fixed_rub)) if customs_duty_eur > 0 else 0.0
        customs_rub = customs_base_rub + customs_fixed_rub

        use_alt_shipping = sp_after_promo_eur > max(0.0, float(settings.shipping_alt_threshold_eur))
        supplier_shipping_rub, supplier_meta = PricingSettingsService._resolve_supplier_rate(
            supplier_id=supplier_id,
            billable_kg=billable_kg,
            use_alt_rate=use_alt_shipping,
            settings=settings,
        )
        delivery_rub = supplier_shipping_rub

        svc_rule = PricingSettingsService._pick_range_rule(
            value=buyout_rub,
            rules=getattr(settings, "svc_rules", []) or [],
            min_key="min_rub",
            max_key="max_rub",
        )
        service_fee_rub, service_fee_meta = PricingSettingsService._compute_rule_amount(buyout_rub, svc_rule)
        subtotal_rub = buyout_rub + payment_fee_rub + insurance_rub + customs_rub + delivery_rub
        markup_multiplier = max(0.0, float(settings.markup_multiplier))
        subtotal_after_markup_rub = (subtotal_rub * markup_multiplier) + service_fee_rub
        tax_rub = subtotal_after_markup_rub * max(0.0, float(settings.tax_rate))
        pass_through_costs_rub = buyout_rub + payment_fee_rub + insurance_rub + customs_rub + delivery_rub
        margin_rub = subtotal_after_markup_rub - pass_through_costs_rub
        raw_final_price_rub = float(subtotal_after_markup_rub + tax_rub)
        final_rounding_mode = PricingSettingsService._normalize_final_rounding_mode(
            getattr(settings, "final_rounding_mode", None),
            default="unit",
        )
        final_price_rub = PricingSettingsService._apply_final_rounding(raw_final_price_rub, final_rounding_mode)

        return ProductPricingComputation(
            final_price_rub=final_price_rub,
            manual_required=False,
            reason=None,
            components={
                "source_price": round(float(source_price), 4),
                "source_currency": currency,
                "source_price_usd": round(sp_usd, 4),
                "source_price_rub": round(sp_rub, 4),
                "source_price_eur": round(sp_eur, 4),
                "buyout_surcharge_value": round(effective_buyout_surcharge_value, 4),
                "buyout_surcharge_currency": effective_buyout_surcharge_currency,
                "buyout_surcharge_rub": round(buyout_surcharge_rub, 4),
                "buyout_surcharge_eur": round(buyout_surcharge_eur, 4),
                "promo_factor": round(promo_applied_factor, 6),
                "promo_factor_source": round(effective_promo_factor, 6),
                "promo_only_no_discount": promo_only_no_discount_enabled,
                "has_source_discount": has_source_discount,
                "sp_after_promo_rub": round(sp_after_promo_rub, 4),
                "sp_after_promo_eur": round(sp_after_promo_eur, 4),
                "buyout_rub": round(buyout_rub, 4),
                "payment_fee_rate": round(float(settings.payment_fee_rate), 6),
                "payment_fee_rub": round(payment_fee_rub, 4),
                "insurance_rub": round(insurance_rub, 4),
                "insurance_mode": insurance_meta.get("mode"),
                "insurance_value": insurance_meta.get("value"),
                "customs_threshold_eur": round(settings.customs_threshold_eur, 4),
                "customs_threshold_currency": settings.customs_threshold_currency,
                "customs_duty_rate": round(settings.customs_duty_rate, 6),
                "customs_processing_rate": round(float(settings.customs_processing_rate), 6),
                "customs_duty_eur": round(customs_duty_eur, 4),
                "customs_processing_eur": round(customs_processing_eur, 4),
                "customs_fixed_rub": round(customs_fixed_rub, 4),
                "customs_duty_rub": round(customs_rub, 4),
                "weight_grams": round(float(weight_grams), 4),
                "weight_tolerance": round(settings.weight_tolerance, 6),
                "effective_weight_grams": round(effective_weight_grams, 4),
                "billable_weight_kg": round(billable_kg, 4),
                "supplier_transport_rub": round(supplier_shipping_rub, 4),
                "delivery_rub": round(delivery_rub, 4),
                "shipping_rule_min_kg": supplier_meta.get("shipping_tariff_min_kg"),
                "shipping_rule_max_kg": supplier_meta.get("shipping_tariff_max_kg"),
                "shipping_rule_label": supplier_meta.get("shipping_tariff_label"),
                "service_fee_rub": round(service_fee_rub, 4),
                "service_fee_mode": service_fee_meta.get("mode"),
                "service_fee_value": service_fee_meta.get("value"),
                "tax_rate": round(float(settings.tax_rate), 6),
                "tax_rub": round(tax_rub, 4),
                "subtotal_rub": round(subtotal_rub, 4),
                "subtotal_after_markup_rub": round(subtotal_after_markup_rub, 4),
                "pass_through_costs_rub": round(pass_through_costs_rub, 4),
                "margin_rub": round(margin_rub, 4),
                "tp_rub": round(subtotal_rub, 4),
                "markup_multiplier": round(markup_multiplier, 6),
                "markup_rate": round(max(0.0, markup_multiplier - 1.0), 6),
                "bybit_usdt_to_rub": round(float(settings.bybit_usdt_to_rub), 6),
                "bybit_extra_rub": round(float(settings.bybit_extra_rub), 6),
                "bybit_bucket_rate_rub": round(float(bybit_bucket_rate), 6),
                "bybit_bucket_step_usdt": int(settings.bybit_bucket_step_usdt),
                "bybit_bucket_max_usdt": int(settings.bybit_bucket_max_usdt),
                "effective_usdt_to_rub": round(effective_usdt_to_rub, 6),
                "eur_to_usd_rate": round(float(settings.eur_to_usd_rate), 6),
                "gbp_to_usd_rate": round(float(settings.gbp_to_usd_rate), 6),
                "jpy_to_usd_rate": round(float(settings.jpy_to_usd_rate), 8),
                "eur_to_rub_effective": round(eur_to_rub_effective, 6),
                "final_rounding_mode": final_rounding_mode,
                "raw_final_price_rub": round(raw_final_price_rub, 4),
                "final_price_rub": final_price_rub,
                **bybit_bucket_meta,
                **supplier_meta,
            },
        )
