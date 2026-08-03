from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models import (
    Product,
    ProductListing,
    ProductListingMember,
    ProductListingVariant,
    Source,
    SourceSetting,
)
from app.schemas.site import (
    SiteCartQuoteItemRequest,
    SiteCartQuoteItemResponse,
    SiteCartQuoteResponse,
    SiteCartQuoteSvcProgressResponse,
    SiteCartQuoteSvcTierResponse,
)
from app.services.settings.pricing_service import PricingSettingsService


@dataclass(slots=True)
class _CartVariant:
    product: Product
    listing: ProductListing
    variant: ProductListingVariant
    source: Source
    source_setting: SourceSetting | None
    quantity: int


class CartPricingService:
    """Build server-authoritative storefront cart quotes."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.pricing = PricingSettingsService(db)

    @staticmethod
    def _is_fixed_final(variant: ProductListingVariant) -> bool:
        return str(variant.pricing_mode or "").strip().lower() == "fixed_final_rub"

    @staticmethod
    def _is_preorder(product: Product) -> bool:
        return str(product.availability_mode or "").strip().lower() == "by_order"

    @staticmethod
    def _money(value: float) -> float:
        return round(float(value), 2)

    @staticmethod
    def _variant_quantity_by_id(
        items: Iterable[SiteCartQuoteItemRequest],
    ) -> tuple[list[int], dict[int, int]]:
        variant_ids: list[int] = []
        quantities: dict[int, int] = {}
        for item in items:
            variant_id = int(item.variant_id)
            if variant_id not in quantities:
                variant_ids.append(variant_id)
                quantities[variant_id] = 0
            quantities[variant_id] += int(item.quantity)
        return variant_ids, quantities

    def _find_variants(
        self, *, variant_ids: list[int], quantities: dict[int, int]
    ) -> dict[int, _CartVariant]:
        rows = (
            self.db.query(
                Product, ProductListing, ProductListingVariant, Source, SourceSetting
            )
            .join(ProductListingMember, ProductListingMember.product_id == Product.id)
            .join(ProductListing, ProductListing.id == ProductListingMember.listing_id)
            .join(
                ProductListingVariant,
                ProductListingVariant.listing_id == ProductListing.id,
            )
            .join(Source, Source.id == ProductListing.source_id)
            .outerjoin(SourceSetting, SourceSetting.source_id == Source.id)
            .filter(ProductListingVariant.id.in_(variant_ids))
            .filter(Product.lifecycle_status == "active")
            .filter(Product.visibility_status == "visible")
            .filter(Product.dedup_status == "independent")
            .filter(ProductListing.orderability_status == "orderable")
            .filter(ProductListingVariant.is_orderable.is_(True))
            .order_by(Product.id.asc())
            .all()
        )
        found: dict[int, _CartVariant] = {}
        for product, listing, variant, source, source_setting in rows:
            variant_id = int(variant.id)
            if variant_id not in found:
                found[variant_id] = _CartVariant(
                    product=product,
                    listing=listing,
                    variant=variant,
                    source=source,
                    source_setting=source_setting,
                    quantity=quantities[variant_id],
                )
        missing = [
            str(variant_id) for variant_id in variant_ids if variant_id not in found
        ]
        if missing:
            raise ValidationError(f"Недоступные варианты: {', '.join(missing)}")
        return found

    def _calculate_source_price(
        self,
        *,
        item: _CartVariant,
        settings,
        buyout_surcharge: bool,
    ):
        source_setting = item.source_setting
        return self.pricing.calculate_for_product(
            source_price=float(item.variant.price_amount)
            if item.variant.price_amount is not None
            else None,
            source_currency=str(item.variant.currency_code or "").upper() or None,
            weight_grams=(
                int(item.product.manual_weight_grams)
                if item.product.manual_weight_grams is not None
                and int(item.product.manual_weight_grams) > 0
                else int(item.listing.source_weight_grams)
                if item.listing.source_weight_grams is not None
                and int(item.listing.source_weight_grams) > 0
                else int(item.product.weight_rule.weight_grams)
                if item.product.weight_rule is not None
                and item.product.weight_rule.weight_grams is not None
                and int(item.product.weight_rule.weight_grams) > 0
                else None
            ),
            supplier_id=(
                int(source_setting.supplier_id)
                if source_setting is not None and source_setting.supplier_id is not None
                else None
            ),
            promo_factor=(
                float(source_setting.promo_factor)
                if source_setting is not None
                else None
            ),
            promo_only_no_discount=(
                bool(source_setting.promo_only_no_discount)
                if source_setting is not None
                else None
            ),
            buyout_surcharge_value=(
                float(source_setting.buyout_surcharge_value)
                if buyout_surcharge
                and source_setting is not None
                and source_setting.buyout_surcharge_value is not None
                else None
            ),
            buyout_surcharge_currency=(
                str(source_setting.buyout_surcharge_currency or "").upper()
                if buyout_surcharge and source_setting is not None
                else None
            ),
            variants=[
                {
                    "price": float(item.variant.price_amount)
                    if item.variant.price_amount is not None
                    else None,
                    "currency": str(item.variant.currency_code or "").upper() or None,
                    "compare_at_price": (
                        float(item.variant.compare_at_price_amount)
                        if item.variant.compare_at_price_amount is not None
                        else None
                    ),
                    "available": True,
                }
            ],
            settings=settings,
        )

    def quote(self, items: list[SiteCartQuoteItemRequest]) -> SiteCartQuoteResponse:
        variant_ids, quantities = self._variant_quantity_by_id(items)
        variants_by_id = self._find_variants(
            variant_ids=variant_ids, quantities=quantities
        )
        settings = self.pricing.get_settings(refresh_bybit=False)
        settings_without_svc = settings.model_copy(update={"svc_rules": []})

        source_surcharge_applied: set[int] = set()
        quote_items: list[SiteCartQuoteItemResponse] = []
        preorder_subtotal_rub = 0.0
        first_dynamic_preorder_index: int | None = None

        for variant_id in variant_ids:
            item = variants_by_id[variant_id]
            quantity = item.quantity
            is_preorder = self._is_preorder(item.product)
            is_fixed_final = self._is_fixed_final(item.variant)

            if is_fixed_final:
                if item.variant.price_amount is None:
                    raise ValidationError(
                        f"Вариант {variant_id} не имеет финальной цены"
                    )
                line_total = float(item.variant.price_amount) * quantity
                quote_items.append(
                    SiteCartQuoteItemResponse(
                        variant_id=variant_id,
                        quantity=quantity,
                        availability="preorder" if is_preorder else "in_stock",
                        original_line_total_rub=self._money(line_total),
                        final_line_total_rub=self._money(line_total),
                    )
                )
                continue

            current = self._calculate_source_price(
                item=item, settings=settings, buyout_surcharge=True
            )
            if current.manual_required or current.final_price_rub is None:
                raise ValidationError(f"Вариант {variant_id} недоступен для расчета")
            original_line_total = float(current.final_price_rub) * quantity

            if not is_preorder:
                quote_items.append(
                    SiteCartQuoteItemResponse(
                        variant_id=variant_id,
                        quantity=quantity,
                        availability="in_stock",
                        original_line_total_rub=self._money(original_line_total),
                        final_line_total_rub=self._money(original_line_total),
                    )
                )
                continue

            base = self._calculate_source_price(
                item=item, settings=settings_without_svc, buyout_surcharge=False
            )
            if base.manual_required or base.final_price_rub is None:
                raise ValidationError(f"Вариант {variant_id} недоступен для расчета")
            source_id = int(item.source.id)
            apply_source_surcharge = source_id not in source_surcharge_applied
            if apply_source_surcharge:
                source_surcharge_applied.add(source_id)
                with_source_surcharge = self._calculate_source_price(
                    item=item,
                    settings=settings_without_svc,
                    buyout_surcharge=True,
                )
                if (
                    with_source_surcharge.manual_required
                    or with_source_surcharge.final_price_rub is None
                ):
                    raise ValidationError(
                        f"Вариант {variant_id} недоступен для расчета"
                    )
            else:
                with_source_surcharge = base

            base_components = base.components or {}
            surcharge_components = with_source_surcharge.components or {}
            base_subtotal = float(base_components.get("subtotal_rub") or 0.0)
            surcharge_subtotal = float(surcharge_components.get("subtotal_rub") or 0.0)
            preorder_subtotal_rub += base_subtotal * quantity
            if apply_source_surcharge:
                preorder_subtotal_rub += surcharge_subtotal - base_subtotal

            final_line_total = float(base.final_price_rub) * quantity
            if apply_source_surcharge:
                final_line_total += float(
                    with_source_surcharge.final_price_rub
                ) - float(base.final_price_rub)
            quote_items.append(
                SiteCartQuoteItemResponse(
                    variant_id=variant_id,
                    quantity=quantity,
                    availability="preorder",
                    original_line_total_rub=self._money(original_line_total),
                    final_line_total_rub=self._money(final_line_total),
                )
            )
            if first_dynamic_preorder_index is None:
                first_dynamic_preorder_index = len(quote_items) - 1

        service_fee_rub, service_fee_meta = self.pricing.calculate_service_fee(
            subtotal_rub=preorder_subtotal_rub,
            rules=[rule.model_dump() if hasattr(rule, "model_dump") else rule for rule in settings.svc_rules],
        )
        if first_dynamic_preorder_index is not None and service_fee_rub > 0:
            selected = quote_items[first_dynamic_preorder_index]
            service_fee_final_rub = service_fee_rub * (
                1.0 + max(0.0, float(settings.tax_rate))
            )
            selected.final_line_total_rub = self._money(
                self.pricing._apply_final_rounding(
                    selected.final_line_total_rub + service_fee_final_rub,
                    settings.final_rounding_mode,
                )
            )

        normalized_rules = self.pricing._normalize_svc_rules(
            [rule.model_dump() if hasattr(rule, "model_dump") else rule for rule in settings.svc_rules]
        )
        svc_tiers = []
        for rule in normalized_rules:
            is_applied = service_fee_meta.get("min_rub") == rule.get(
                "min_rub"
            ) and service_fee_meta.get("max_rub") == rule.get("max_rub")
            svc_tiers.append(
                SiteCartQuoteSvcTierResponse(
                    min_rub=float(rule["min_rub"]),
                    max_rub=(
                        float(rule["max_rub"])
                        if rule.get("max_rub") is not None
                        else None
                    ),
                    mode=str(rule["mode"]),
                    value=float(rule["value"]),
                    amount_rub=(self._money(service_fee_rub) if is_applied else None),
                    is_applied=is_applied,
                )
            )
        next_threshold_rub = next(
            (
                float(rule["min_rub"])
                for rule in normalized_rules
                if float(rule["min_rub"]) > preorder_subtotal_rub
            ),
            None,
        )
        original_total_rub = sum(item.original_line_total_rub for item in quote_items)
        final_total_rub = sum(item.final_line_total_rub for item in quote_items)
        return SiteCartQuoteResponse(
            items=quote_items,
            original_total_rub=self._money(original_total_rub),
            final_total_rub=self._money(final_total_rub),
            total_rub=self._money(final_total_rub),
            svc_tiers=svc_tiers,
            svc_progress=SiteCartQuoteSvcProgressResponse(
                preorder_subtotal_rub=self._money(preorder_subtotal_rub),
                applied_amount_rub=self._money(service_fee_rub),
                next_threshold_rub=(
                    self._money(next_threshold_rub)
                    if next_threshold_rub is not None
                    else None
                ),
            ),
        )
