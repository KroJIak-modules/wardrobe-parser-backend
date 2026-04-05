"""Pricing settings CRUD and final price calculation by TZ formula."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ParserSupplier
from app.repositories import ParserPricingSettingsRepository, ParserSourceRepository, ParserSupplierRepository
from app.schemas.parser import (
    PricingSettingsResponse,
    PricingSettingsUpdateRequest,
    PricingSupplierCreateRequest,
    PricingSupplierResponse,
    PricingSupplierUpdateRequest,
)

_FORMULA_LINES = [
    "CDT_EUR = max(0, (SP_EUR - THR_EUR)) * DUTY",
    "STEP = ceil((WEIGHT_G * WT) / 500)",
    "STC_RUB = SSR[SUPPLIER][STEP]",
    "BSC_RUB = buyout surcharge in source currency converted to RUB",
    "SP_AFTER_PROMO_RUB = SP_RUB * PROMO",
    "TP_RUB = SP_AFTER_PROMO_RUB + BSC_RUB + CDT_RUB + SDC_RUB + STC_RUB",
    "FP_RUB = ceil(TP_RUB * MP)",
]

_FORMULA_LATEX = (
    r"\left\lceil\left(SP_{RUB}\cdot PROMO + BSC_{RUB} + \max(0,SP_{EUR}-THR_{EUR})\cdot DUTY\cdot EUR2RUB + "
    r"SDC_{RUB} + SSR_{SUPPLIER,\left\lceil\frac{WEIGHT_G\cdot WT}{500}\right\rceil}\right)\cdot MP\right\rceil"
)

_FORMULA_LEGEND = [
    {"key": "SP", "description": "Seller price (исходная цена товара)"},
    {"key": "SP_EUR", "description": "Цена товара, приведенная к EUR"},
    {"key": "SP_RUB", "description": "Цена товара, приведенная к RUB"},
    {"key": "THR_EUR", "description": "Порог для расчета пошлины в EUR"},
    {"key": "DUTY", "description": "Ставка пошлины"},
    {"key": "EUR2RUB", "description": "Курс EUR к RUB"},
    {"key": "CDT_EUR", "description": "Пошлина в EUR"},
    {"key": "CDT_RUB", "description": "Пошлина в RUB"},
    {"key": "SDC_RUB", "description": "Доставка от продавца в RUB"},
    {"key": "STC_RUB", "description": "Транспортировка поставщиком в RUB"},
    {"key": "BSC_RUB", "description": "Доплата к стоимости выкупа в RUB"},
    {"key": "MP", "description": "Коэффициент наценки"},
    {"key": "WT", "description": "Коэффициент погрешности веса"},
    {"key": "WEIGHT_G", "description": "Вес товара в граммах"},
    {"key": "STEP", "description": "Вычисленный номер шага веса для SSR"},
    {"key": "PROMO", "description": "Коэффициент промокода"},
    {"key": "SUPPLIER", "description": "Поставщик, привязанный к источнику магазина"},
    {"key": "SSR[SUPPLIER][STEP]", "description": "Тариф по таблице поставщика и шагу веса"},
    {"key": "FP_RUB", "description": "Финальная цена для витрины"},
]


@dataclass(slots=True)
class ProductPricingComputation:
    final_price_rub: float | None
    manual_required: bool
    reason: str | None
    components: dict[str, Any]


class PricingSettingsService:
    """Manage pricing settings and calculate final customer price."""

    def __init__(self, db: Session):
        self.db = db
        self.repo = ParserPricingSettingsRepository(db)
        self.supplier_repo = ParserSupplierRepository(db)
        self.source_repo = ParserSourceRepository(db)

    def _bootstrap_suppliers(self) -> bool:
        changed = False
        if self.supplier_repo.get_by_key("default") is None:
            changed = True
        default_supplier = self.supplier_repo.get_default_supplier()
        if not default_supplier.shipping_rates:
            self.supplier_repo.ensure_linear_rates(
                supplier_id=default_supplier.id,
                per_500g_rub=0.0,
                max_step_500g=120,
            )
            self.db.flush()
            changed = True
        return changed

    @staticmethod
    def _normalize_currency(raw: str | None, *, default: str = "RUB") -> str:
        value = (raw or default).strip().upper()
        if value not in {"RUB", "USD", "EUR"}:
            return default
        return value

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

    def get_settings(self) -> PricingSettingsResponse:
        bootstrap_changed = self._bootstrap_suppliers()
        entity, created = self.repo.get_or_create_default()
        if created or bootstrap_changed:
            self.db.commit()
            self.db.refresh(entity)
        suppliers = self.supplier_repo.list_all_with_rates()
        return self._to_response(entity, suppliers=suppliers)

    def update_settings(self, payload: PricingSettingsUpdateRequest) -> PricingSettingsResponse:
        self._bootstrap_suppliers()
        entity, created = self.repo.get_or_create_default()
        patch = payload.model_dump(exclude_none=True)
        if "customs_threshold_currency" in patch:
            patch["customs_threshold_currency"] = self._normalize_currency(
                patch.get("customs_threshold_currency"),
                default=self._normalize_currency(getattr(entity, "customs_threshold_currency", None), default="EUR"),
            )
        for key, value in patch.items():
            setattr(entity, key, value)
        self.db.commit()
        if created or patch:
            self.db.refresh(entity)
        suppliers = self.supplier_repo.list_all_with_rates()
        return self._to_response(entity, suppliers=suppliers)

    @staticmethod
    def _to_response(entity, *, suppliers: list[ParserSupplier]) -> PricingSettingsResponse:
        return PricingSettingsResponse(
            markup_multiplier=float(entity.markup_multiplier),
            weight_tolerance=float(entity.weight_tolerance),
            promo_factor=float(entity.promo_factor),
            customs_threshold_eur=float(entity.customs_threshold_eur),
            customs_threshold_currency=PricingSettingsService._normalize_currency(
                getattr(entity, "customs_threshold_currency", None),
                default="EUR",
            ),
            customs_duty_rate=float(entity.customs_duty_rate),
            seller_delivery_rub=float(entity.seller_delivery_rub),
            usd_to_rub=float(entity.usd_to_rub),
            eur_to_rub=float(entity.eur_to_rub),
            suppliers=[
                PricingSettingsService._supplier_to_response(
                    item,
                    usd_to_rub=float(entity.usd_to_rub),
                    eur_to_rub=float(entity.eur_to_rub),
                )
                for item in suppliers
            ],
            formula_latex=_FORMULA_LATEX,
            formula_lines=list(_FORMULA_LINES),
            formula_legend=[dict(item) for item in _FORMULA_LEGEND],
        )

    @staticmethod
    def _supplier_to_response(
        supplier: ParserSupplier,
        *,
        usd_to_rub: float,
        eur_to_rub: float,
    ) -> PricingSupplierResponse:
        rates = sorted(supplier.shipping_rates, key=lambda item: item.step_500g)
        if rates:
            first_step = max(1, int(rates[0].step_500g))
            rate_per_500g = float(rates[0].rate_rub) / float(first_step)
        else:
            rate_per_500g = 0.0
        rate_currency = PricingSettingsService._normalize_currency(getattr(supplier, "rate_currency", None), default="RUB")
        rate_per_500g_value = PricingSettingsService._from_rub(
            rate_per_500g,
            rate_currency,
            usd_to_rub=usd_to_rub,
            eur_to_rub=eur_to_rub,
        )
        max_step = int(rates[-1].step_500g) if rates else 0
        return PricingSupplierResponse(
            id=int(supplier.id),
            key=supplier.key,
            name=supplier.name,
            country_code=supplier.country_code,
            country_name=supplier.country_name,
            rate_currency=rate_currency,
            rate_per_500g_value=rate_per_500g_value,
            rate_per_500g_rub=rate_per_500g,
            max_step_500g=max_step,
            rates=[{"step_500g": int(rate.step_500g), "rate_rub": float(rate.rate_rub)} for rate in rates],
        )

    def update_supplier(self, supplier_id: int, payload: PricingSupplierUpdateRequest) -> PricingSupplierResponse:
        self._bootstrap_suppliers()
        supplier = self.supplier_repo.get_by_id(supplier_id)
        if supplier is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Поставщик не найден")

        patch = payload.model_dump(exclude_none=True)
        settings_entity, _ = self.repo.get_or_create_default()
        for key in ("name", "country_code", "country_name"):
            if key in patch:
                setattr(supplier, key, patch[key])
        if "rate_currency" in patch:
            supplier.rate_currency = self._normalize_currency(patch.get("rate_currency"), default=supplier.rate_currency)

        current_rates = sorted(supplier.shipping_rates, key=lambda item: item.step_500g)
        if current_rates:
            first_step = max(1, int(current_rates[0].step_500g))
            current_rate_per_500g = float(current_rates[0].rate_rub) / float(first_step)
        else:
            current_rate_per_500g = 0.0
        current_max_step = int(current_rates[-1].step_500g) if current_rates else 120
        active_currency = self._normalize_currency(
            patch.get("rate_currency"),
            default=self._normalize_currency(getattr(supplier, "rate_currency", None), default="RUB"),
        )
        if "rate_per_500g_value" in patch:
            target_rate_per_500g = self._to_rub(
                float(patch.get("rate_per_500g_value")),
                active_currency,
                usd_to_rub=float(settings_entity.usd_to_rub),
                eur_to_rub=float(settings_entity.eur_to_rub),
            )
        else:
            target_rate_per_500g = float(patch.get("rate_per_500g_rub", current_rate_per_500g))
        target_max_step = int(patch.get("max_step_500g", current_max_step))
        if "rate_per_500g_rub" in patch or "rate_per_500g_value" in patch or "max_step_500g" in patch:
            self.supplier_repo.ensure_linear_rates(
                supplier_id=supplier.id,
                per_500g_rub=target_rate_per_500g,
                max_step_500g=target_max_step,
            )

        self.db.commit()
        refreshed = self.supplier_repo.get_by_id(supplier.id)
        if refreshed is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Поставщик не найден после обновления")
        self.db.refresh(refreshed)
        return self._supplier_to_response(
            refreshed,
            usd_to_rub=float(settings_entity.usd_to_rub),
            eur_to_rub=float(settings_entity.eur_to_rub),
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
            country_code=payload.country_code.strip().upper(),
            country_name=payload.country_name.strip(),
            rate_currency=self._normalize_currency(payload.rate_currency, default="RUB"),
        )
        self.supplier_repo.flush()
        settings_entity, _ = self.repo.get_or_create_default()
        per_500g_rub = self._to_rub(
            float(payload.rate_per_500g_value),
            self._normalize_currency(payload.rate_currency, default="RUB"),
            usd_to_rub=float(settings_entity.usd_to_rub),
            eur_to_rub=float(settings_entity.eur_to_rub),
        )
        self.supplier_repo.ensure_linear_rates(
            supplier_id=supplier.id,
            per_500g_rub=per_500g_rub,
            max_step_500g=int(payload.max_step_500g),
        )
        self.db.commit()
        created = self.supplier_repo.get_by_id(int(supplier.id))
        if created is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось создать поставщика")
        return self._supplier_to_response(
            created,
            usd_to_rub=float(settings_entity.usd_to_rub),
            eur_to_rub=float(settings_entity.eur_to_rub),
        )

    def delete_supplier(self, supplier_id: int) -> dict[str, str]:
        supplier = self.supplier_repo.get_by_id(supplier_id)
        if supplier is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Поставщик не найден")
        if supplier.key == "default":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нельзя удалить поставщика default")

        assigned_sources = self.source_repo.count_by_supplier_id(supplier.id)
        if assigned_sources > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Нельзя удалить: поставщик назначен на {assigned_sources} источников",
            )
        try:
            self.db.delete(supplier)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Нельзя удалить поставщика: есть связанные записи")
        return {"status": "ok", "message": "Поставщик удален"}

    @staticmethod
    def _resolve_supplier_rate(
        *,
        supplier_id: int | None,
        shipping_steps: int,
        settings: PricingSettingsResponse,
    ) -> tuple[float, dict[str, Any]]:
        suppliers = settings.suppliers or []
        selected = next((item for item in suppliers if supplier_id is not None and item.id == supplier_id), None)
        if selected is None and suppliers:
            selected = next((item for item in suppliers if item.key == "default"), suppliers[0])
        if selected is None:
            return 0.0, {"supplier_id": supplier_id, "supplier_key": None, "supplier_name": None, "rate_mode": "missing_supplier"}

        rates = {int(item.step_500g): float(item.rate_rub) for item in selected.rates}
        if shipping_steps in rates:
            return rates[shipping_steps], {
                "supplier_id": selected.id,
                "supplier_key": selected.key,
                "supplier_name": selected.name,
                "rate_mode": "exact_step",
                "supplier_rate_step_500g": shipping_steps,
            }

        rate_per_500g = float(selected.rate_per_500g_rub)
        if rate_per_500g <= 0 and rates:
            smallest_step = min(rates.keys())
            rate_per_500g = float(rates[smallest_step]) / float(max(1, smallest_step))
        estimated = float(shipping_steps) * max(0.0, rate_per_500g)
        return estimated, {
            "supplier_id": selected.id,
            "supplier_key": selected.key,
            "supplier_name": selected.name,
            "rate_mode": "estimated_linear",
            "supplier_rate_step_500g": shipping_steps,
            "supplier_rate_per_500g_rub": round(rate_per_500g, 4),
        }

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
        seller_delivery_rub: float | None,
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

        sp_rub: float | None = None
        sp_eur: float | None = None
        if currency == "RUB":
            sp_rub = float(source_price)
            if settings.eur_to_rub > 0:
                sp_eur = sp_rub / settings.eur_to_rub
        elif currency == "USD":
            sp_rub = float(source_price) * settings.usd_to_rub
            if settings.eur_to_rub > 0:
                sp_eur = sp_rub / settings.eur_to_rub
        elif currency == "EUR":
            sp_eur = float(source_price)
            sp_rub = sp_eur * settings.eur_to_rub
        else:
            return ProductPricingComputation(
                None,
                True,
                "unsupported_currency",
                {"source_price": float(source_price), "source_currency": currency or None},
            )

        if sp_rub is None or sp_eur is None:
            return ProductPricingComputation(
                None,
                True,
                "invalid_fx_settings",
                {"source_price": float(source_price), "source_currency": currency or None},
            )

        effective_weight_grams = max(1.0, float(weight_grams) * settings.weight_tolerance)
        shipping_steps = max(1, math.ceil(effective_weight_grams / 500.0))
        effective_buyout_surcharge_value = max(0.0, float(buyout_surcharge_value or 0.0))
        effective_buyout_surcharge_currency = PricingSettingsService._normalize_currency(
            buyout_surcharge_currency,
            default=currency if currency in {"RUB", "USD", "EUR"} else "RUB",
        )
        buyout_surcharge_rub = PricingSettingsService._to_rub(
            effective_buyout_surcharge_value,
            effective_buyout_surcharge_currency,
            usd_to_rub=float(settings.usd_to_rub),
            eur_to_rub=float(settings.eur_to_rub),
        )
        buyout_surcharge_eur = buyout_surcharge_rub / settings.eur_to_rub if settings.eur_to_rub > 0 else 0.0

        effective_seller_delivery_rub = float(settings.seller_delivery_rub if seller_delivery_rub is None else seller_delivery_rub)
        effective_seller_delivery_rub = max(0.0, effective_seller_delivery_rub)
        effective_promo_factor = max(0.0, float(settings.promo_factor if promo_factor is None else promo_factor))
        promo_only_no_discount_enabled = bool(promo_only_no_discount)
        has_source_discount = PricingSettingsService._has_discount_in_variants(variants)
        promo_applied_factor = 1.0 if promo_only_no_discount_enabled and has_source_discount else effective_promo_factor

        cdt_eur = max(0.0, sp_eur - settings.customs_threshold_eur) * settings.customs_duty_rate
        cdt_rub = cdt_eur * settings.eur_to_rub
        stc_rub, supplier_meta = PricingSettingsService._resolve_supplier_rate(
            supplier_id=supplier_id,
            shipping_steps=shipping_steps,
            settings=settings,
        )

        sp_after_promo_rub = sp_rub * promo_applied_factor
        tp_rub = sp_after_promo_rub + buyout_surcharge_rub + cdt_rub + effective_seller_delivery_rub + stc_rub
        final_price_rub = float(math.ceil(tp_rub * settings.markup_multiplier))

        return ProductPricingComputation(
            final_price_rub=final_price_rub,
            manual_required=False,
            reason=None,
            components={
                "source_price": round(float(source_price), 4),
                "source_currency": currency,
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
                "customs_threshold_eur": round(settings.customs_threshold_eur, 4),
                "customs_threshold_currency": settings.customs_threshold_currency,
                "customs_duty_rate": round(settings.customs_duty_rate, 6),
                "customs_duty_eur": round(cdt_eur, 4),
                "customs_duty_rub": round(cdt_rub, 4),
                "weight_grams": round(float(weight_grams), 4),
                "weight_tolerance": round(settings.weight_tolerance, 6),
                "effective_weight_grams": round(effective_weight_grams, 4),
                "shipping_steps_500g": shipping_steps,
                "supplier_transport_rub": round(stc_rub, 4),
                "seller_delivery_rub": round(effective_seller_delivery_rub, 4),
                "markup_multiplier": round(settings.markup_multiplier, 6),
                "tp_rub": round(tp_rub, 4),
                "final_price_rub": final_price_rub,
                **supplier_meta,
            },
        )
