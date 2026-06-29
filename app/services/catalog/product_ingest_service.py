from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.source_identity import normalize_host, normalize_listing_url
from app.core.exceptions import ValidationError
from app.models import Product, ProductListing, WeightRule, WeightRuleKeyword
from app.repositories.catalog_products import CatalogProductRepository
from app.services.catalog.filter_assignment_service import ProductFilterAssignmentService
from app.services.settings.weight_rule_matcher import WeightRuleMatcherEntry, WeightRuleMatcherField, resolve_match_for_fields


@dataclass(slots=True)
class BatchApplyResult:
    listings_seen: int = 0
    listings_applied: int = 0


class ProductIngestService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = CatalogProductRepository(db)

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _normalize_orderability(status: str | None) -> str:
        value = str(status or "").strip().lower()
        if value == "orderable":
            return "orderable"
        if value == "sold_out":
            return "sold_out"
        return "unavailable"

    @staticmethod
    def _positive_int(value: object) -> int | None:
        try:
            candidate = int(value)  # type: ignore[arg-type]
        except Exception:
            return None
        return candidate if candidate > 0 else None

    @staticmethod
    def _normalized_optional_text(value: object) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _normalized_variant_currency_code(variant: object) -> str | None:
        if isinstance(variant, dict):
            raw_currency = variant.get("currency_code")
            if raw_currency is None or str(raw_currency).strip() == "":
                raw_currency = variant.get("currency")
        else:
            raw_currency = getattr(variant, "currency_code", None)
            if raw_currency is None or str(raw_currency).strip() == "":
                raw_currency = getattr(variant, "currency", None)
        return str(raw_currency or "").strip().upper() or None

    @classmethod
    def _all_variants_in_rub(cls, variants: list[object] | tuple[object, ...]) -> bool:
        has_variants = False
        for variant in variants:
            has_variants = True
            if cls._normalized_variant_currency_code(variant) != "RUB":
                return False
        return has_variants

    @classmethod
    def _weight_is_not_required_for_listing(
        cls,
        *,
        listing: ProductListing,
        variant_payloads: list[dict] | None = None,
    ) -> bool:
        if variant_payloads is not None:
            return cls._all_variants_in_rub(variant_payloads)
        return cls._all_variants_in_rub(list(getattr(listing, "variants", []) or []))

    @classmethod
    def _normalized_text_list(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for raw_item in value:
            item = cls._normalized_optional_text(raw_item)
            if item is None or item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    @classmethod
    def _prefer_existing_text(cls, incoming: object, existing: str | None) -> str | None:
        incoming_text = cls._normalized_optional_text(incoming)
        if incoming_text is not None:
            return incoming_text
        return cls._normalized_optional_text(existing)

    @classmethod
    def _prefer_existing_text_list(cls, incoming: object, existing: object) -> list[str]:
        incoming_items = cls._normalized_text_list(incoming)
        if incoming_items:
            return incoming_items
        return cls._normalized_text_list(existing)

    @classmethod
    def _prefer_existing_positive_int(cls, incoming: object, existing: int | None) -> int | None:
        incoming_value = cls._positive_int(incoming)
        if incoming_value is not None:
            return incoming_value
        return cls._positive_int(existing)

    @staticmethod
    def _normalize_gender(value: object) -> str:
        candidate = str(value or "").strip().lower()
        if candidate in {"male", "female", "unisex"}:
            return candidate
        return "unisex"

    def _resolve_keyword_weight_rule(self, listing: ProductListing) -> tuple[int, int] | None:
        fields = [
            WeightRuleMatcherField(name="title", text=listing.source_title, weight=8),
            WeightRuleMatcherField(name="category", text=listing.source_category_raw, weight=7),
            WeightRuleMatcherField(name="handle", text=listing.handle, weight=6),
            WeightRuleMatcherField(name="designer", text=listing.source_designer_raw, weight=2),
            WeightRuleMatcherField(name="description_text", text=listing.source_description_text, weight=1),
            WeightRuleMatcherField(name="description_html", text=listing.source_description_html, weight=1),
        ]
        if not any(str(field.text or "").strip() for field in fields):
            return None

        rules = (
            self.db.query(WeightRule)
            .filter(WeightRule.is_enabled.is_(True))
            .order_by(WeightRule.id.asc())
            .all()
        )
        match = resolve_match_for_fields(
            rules=[
                WeightRuleMatcherEntry(
                    rule_id=int(rule.id),
                    weight_grams=int(rule.weight_grams),
                    keywords=[
                        str(keyword.keyword or "")
                        for keyword in (
                            self.db.query(WeightRuleKeyword)
                            .filter(WeightRuleKeyword.rule_id == int(rule.id))
                            .order_by(WeightRuleKeyword.id.asc())
                            .all()
                        )
                    ],
                )
                for rule in rules
            ],
            fields=fields,
        )
        if match.rule_id is None or match.weight_grams is None:
            return None
        return int(match.rule_id), int(match.weight_grams)

    def _resolve_listing_status(
        self,
        *,
        product: Product,
        listing: ProductListing,
        incoming_status: str,
        incoming_reason: str | None,
        incoming_reasons: list[str],
        variant_payloads: list[dict] | None = None,
    ) -> tuple[str, str | None]:
        reasons = [reason for reason in incoming_reasons if reason]
        if incoming_reason and incoming_reason not in reasons:
            reasons.append(incoming_reason)

        effective_listing = product.primary_listing or listing
        if self._weight_is_not_required_for_listing(listing=effective_listing, variant_payloads=variant_payloads):
            product.weight_rule_id = None
            reasons = [reason for reason in reasons if reason != "missing_weight"]
        else:
            source_weight = self._positive_int(effective_listing.source_weight_grams)
            manual_weight = self._positive_int(product.manual_weight_grams)
            if manual_weight is not None:
                product.weight_rule_id = None
                reasons = [reason for reason in reasons if reason != "missing_weight"]
            elif source_weight is not None:
                product.weight_rule_id = None
                reasons = [reason for reason in reasons if reason != "missing_weight"]
            else:
                keyword_rule = self._resolve_keyword_weight_rule(effective_listing)
                if keyword_rule is not None:
                    product.weight_rule_id = int(keyword_rule[0])
                    reasons = [reason for reason in reasons if reason != "missing_weight"]
                else:
                    product.weight_rule_id = None
                    if "missing_weight" not in reasons:
                        reasons.append("missing_weight")

        if reasons:
            return "unavailable", reasons[0]
        if incoming_status == "unavailable":
            return "unavailable", None
        return incoming_status, None

    @staticmethod
    def _normalize_status_reasons(item: dict) -> list[str]:
        raw_reasons = item.get("status_reasons") if isinstance(item.get("status_reasons"), list) else []
        reasons: list[str] = []
        seen: set[str] = set()
        for raw_reason in raw_reasons:
            reason = str(raw_reason or "").strip().lower()
            if not reason or reason in seen:
                continue
            seen.add(reason)
            reasons.append(reason)
        reason_text = str(item.get("status_reason") or "").strip().lower()
        if reason_text and reason_text not in seen:
            reasons.append(reason_text)
        return reasons

    def _resolve_product(self, *, listing_id: int, target_product_id: int | None) -> Product | None:
        if target_product_id is not None:
            product = self.products.get_product(int(target_product_id))
            if product is None:
                raise ValidationError(f"target product not found: {target_product_id}")
            self.products.ensure_membership(product_id=int(product.id), listing_id=int(listing_id))
            return product
        return self.products.get_product_by_listing(int(listing_id))

    @staticmethod
    def _variant_payloads(item: dict) -> list[dict]:
        variants_raw = item.get("variants") if isinstance(item.get("variants"), list) else []
        variants: list[dict] = []
        for variant in variants_raw:
            if not isinstance(variant, dict):
                continue
            source_ref = variant.get("source_ref") if isinstance(variant.get("source_ref"), dict) else {}
            raw_price = variant.get("price")
            if raw_price is None or str(raw_price).strip() == "":
                raw_price = variant.get("price_amount")
            raw_compare_at_price = variant.get("compare_at_price")
            if raw_compare_at_price is None or str(raw_compare_at_price).strip() == "":
                raw_compare_at_price = variant.get("compare_at_price_amount")
            raw_currency = variant.get("currency")
            if raw_currency is None or str(raw_currency).strip() == "":
                raw_currency = variant.get("currency_code")
            variants.append(
                {
                    "source_ref_id": str(source_ref.get("id") or "").strip() or None,
                    "sku": str(source_ref.get("sku") or variant.get("sku") or "").strip() or None,
                    "title": str(variant.get("title") or "").strip() or "Default",
                    "price_amount": (
                        Decimal(str(raw_price))
                        if raw_price is not None and str(raw_price).strip() != ""
                        else None
                    ),
                    "compare_at_price_amount": (
                        Decimal(str(raw_compare_at_price))
                        if raw_compare_at_price is not None and str(raw_compare_at_price).strip() != ""
                        else None
                    ),
                    "currency_code": str(raw_currency or "").strip().upper() or None,
                    "pricing_mode": "source",
                    "is_orderable": bool(variant.get("available", variant.get("is_orderable", True))),
                }
            )
        return variants

    @staticmethod
    def _image_urls(item: dict) -> list[str]:
        images = item.get("images") if isinstance(item.get("images"), list) else []
        out: list[str] = []
        seen: set[str] = set()
        for raw in images:
            value = str(raw or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            out.append(value)
        return out

    def apply_batch(
        self,
        *,
        source_id: int,
        items: list[dict],
        reconcile_missing: bool = False,
        target_product_id: int | None = None,
        force_primary_listing: bool = False,
    ) -> BatchApplyResult:
        result = BatchApplyResult(listings_seen=0, listings_applied=0)
        seen_listing_ids: set[int] = set()
        affected_product_ids: set[int] = set()

        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            result.listings_seen += 1
            external_id = None
            source_ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
            if source_ref:
                external_id = str(source_ref.get("external_id") or "").strip() or None

            listing = self.products.get_listing_by_source_identity(source_id=source_id, external_id=external_id, url=url)
            if listing is None:
                listing = self.products.create_listing(
                    source_id=int(source_id),
                    external_id=external_id,
                    url=url,
                    url_normalized=normalize_listing_url(url),
                    host_normalized=normalize_host(url),
                    handle=self._normalized_optional_text(item.get("handle")),
                    source_title=str(item.get("title") or "").strip() or url,
                    source_description_html=self._normalized_optional_text(item.get("description_html")),
                    source_description_text=self._normalized_optional_text(item.get("description")),
                    source_weight_grams=self._positive_int(item.get("source_weight_grams")),
                    source_designer_raw=self._normalized_optional_text(item.get("designer")),
                    source_category_raw=self._normalized_optional_text(item.get("category")),
                    source_tags=self._normalized_text_list(item.get("tags")),
                    ingest_mode="sync",
                    last_seen_at=self._utcnow(),
                    last_synced_at=self._utcnow(),
                )
            else:
                listing.external_id = external_id
                listing.url = url
                listing.url_normalized = normalize_listing_url(url)
                listing.host_normalized = normalize_host(url)
                listing.handle = self._normalized_optional_text(item.get("handle"))
                listing.source_title = str(item.get("title") or "").strip() or url
                listing.source_description_html = self._normalized_optional_text(item.get("description_html"))
                listing.source_description_text = self._normalized_optional_text(item.get("description"))
                # Optional source metadata must not degrade to blank because one parser pass missed it.
                listing.source_weight_grams = self._prefer_existing_positive_int(item.get("source_weight_grams"), listing.source_weight_grams)
                listing.source_designer_raw = self._prefer_existing_text(item.get("designer"), listing.source_designer_raw)
                listing.source_category_raw = self._prefer_existing_text(item.get("category"), listing.source_category_raw)
                listing.source_tags = self._prefer_existing_text_list(item.get("tags"), listing.source_tags)
                listing.last_seen_at = self._utcnow()
                listing.last_synced_at = self._utcnow()

            previous_owner = self.products.get_product_by_listing(int(listing.id))
            product = self._resolve_product(listing_id=int(listing.id), target_product_id=target_product_id)
            normalized_gender = self._normalize_gender(item.get("gender"))
            if product is None:
                source_setting = getattr(getattr(listing, "source", None), "setting", None)
                visibility_status = "hidden" if bool(getattr(source_setting, "hide_auto_added_products", False)) else "visible"
                product = self.products.create_product(
                    gender=normalized_gender,
                    source_gender=normalized_gender,
                    gender_is_manual=False,
                    availability_mode="by_order",
                    lifecycle_status="active",
                    visibility_status=visibility_status,
                )
                self.products.ensure_membership(product_id=int(product.id), listing_id=int(listing.id))
                product.primary_listing_id = int(listing.id)
            elif target_product_id is not None and force_primary_listing:
                product.primary_listing_id = int(listing.id)
            product.source_gender = normalized_gender
            if not bool(getattr(product, "gender_is_manual", False)):
                product.gender = normalized_gender
            if previous_owner is not None:
                affected_product_ids.add(int(previous_owner.id))
            affected_product_ids.add(int(product.id))

            variants = self._variant_payloads(item)
            incoming_status = self._normalize_orderability(str(item.get("orderability_status") or "unavailable"))
            incoming_reason = str(item.get("status_reason") or "").strip() or None
            incoming_reasons = self._normalize_status_reasons(item)
            listing.orderability_status, listing.status_reason = self._resolve_listing_status(
                product=product,
                listing=listing,
                incoming_status=incoming_status,
                incoming_reason=incoming_reason,
                incoming_reasons=incoming_reasons,
                variant_payloads=variants,
            )
            self.products.replace_variants(listing_id=int(listing.id), variants=variants)
            listing_images, stale_listing_image_ids = self.products.replace_listing_images(
                listing_id=int(listing.id),
                image_urls=self._image_urls(item),
            )
            self.products.sync_gallery_scope_with_source_images(
                product_id=int(product.id),
                listing_id=int(listing.id),
                listing_images=listing_images,
            )
            self.products.delete_listing_images(listing_image_ids=stale_listing_image_ids)

            if product.primary_listing_id is None:
                product.primary_listing_id = int(listing.id)

            seen_listing_ids.add(int(listing.id))
            result.listings_applied += 1

        if reconcile_missing:
            stale_listings = self.products.list_source_listings(int(source_id), ingest_mode="sync")
            for listing in stale_listings:
                if int(listing.id) in seen_listing_ids:
                    continue
                listing.orderability_status = "unavailable"
                listing.status_reason = "source_removed"
                product = self.products.get_product_by_listing(int(listing.id))
                if product is None or int(product.primary_listing_id or 0) != int(listing.id):
                    continue
                affected_product_ids.add(int(product.id))
                siblings = [
                    member_listing
                    for member_listing in self.products.list_product_listings(int(product.id))
                    if int(member_listing.id) != int(listing.id)
                ]
                if siblings:
                    product.primary_listing_id = int(siblings[0].id)

        self.db.flush()
        ProductFilterAssignmentService(self.db).enqueue_product_ids_after_commit(affected_product_ids)
        return result
