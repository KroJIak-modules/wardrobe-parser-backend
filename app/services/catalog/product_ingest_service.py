from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import re

from sqlalchemy.orm import Session

from app.models import Product, ProductListing, WeightRule, WeightRuleKeyword
from app.repositories.catalog_products import CatalogProductRepository


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
        if value == "available":
            return "orderable"
        if value in {"out_of_stock", "sold_out"}:
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
    def _normalize_gender(value: object) -> str:
        candidate = str(value or "").strip().lower()
        if candidate in {"male", "female", "unisex"}:
            return candidate
        return "unisex"

    @staticmethod
    def _match_count(text: str, keyword: str) -> int:
        normalized_keyword = str(keyword or "").strip().lower()
        if not normalized_keyword:
            return 0
        pattern = rf"(?<!\w){re.escape(normalized_keyword)}(?!\w)"
        return 1 if re.search(pattern, text) else 0

    def _resolve_keyword_weight_rule(self, listing: ProductListing) -> tuple[int, int] | None:
        haystack = " ".join(
            [
                str(listing.source_title or ""),
                str(listing.source_description_text or ""),
                str(listing.source_description_html or ""),
                str(listing.source_designer_raw or ""),
                str(listing.source_category_raw or ""),
            ]
        ).lower()
        if not haystack.strip():
            return None

        rules = (
            self.db.query(WeightRule)
            .filter(WeightRule.is_enabled.is_(True))
            .order_by(WeightRule.id.asc())
            .all()
        )

        best_rule_id: int | None = None
        best_weight_grams: int | None = None
        best_hits = 0
        for rule in rules:
            keywords = (
                self.db.query(WeightRuleKeyword)
                .filter(WeightRuleKeyword.rule_id == int(rule.id))
                .order_by(WeightRuleKeyword.id.asc())
                .all()
            )
            hits = sum(self._match_count(haystack, str(keyword.keyword or "")) for keyword in keywords)
            if hits <= 0:
                continue
            if hits > best_hits or (hits == best_hits and best_rule_id is not None and int(rule.id) < best_rule_id):
                best_hits = hits
                best_rule_id = int(rule.id)
                best_weight_grams = int(rule.weight_grams)
        if best_rule_id is None or best_weight_grams is None:
            return None
        return best_rule_id, best_weight_grams

    def _resolve_listing_status(
        self,
        *,
        product: Product,
        listing: ProductListing,
        incoming_status: str,
        incoming_reason: str | None,
    ) -> tuple[str, str | None]:
        source_weight = self._positive_int(listing.source_weight_grams)
        manual_weight = self._positive_int(product.manual_weight_grams)
        if manual_weight is not None:
            product.weight_rule_id = None
            return incoming_status, incoming_reason
        if source_weight is not None:
            product.weight_rule_id = None
            return incoming_status, incoming_reason

        keyword_rule = self._resolve_keyword_weight_rule(listing)
        if keyword_rule is not None:
            product.weight_rule_id = int(keyword_rule[0])
            return incoming_status, incoming_reason
        product.weight_rule_id = None
        return "unavailable", "missing_weight"

    @staticmethod
    def _variant_payloads(item: dict) -> list[dict]:
        variants_raw = item.get("variants") if isinstance(item.get("variants"), list) else []
        variants: list[dict] = []
        for variant in variants_raw:
            if not isinstance(variant, dict):
                continue
            source_ref = variant.get("source_ref") if isinstance(variant.get("source_ref"), dict) else {}
            variants.append(
                {
                    "source_ref_id": str(source_ref.get("id") or "").strip() or None,
                    "sku": str(source_ref.get("sku") or variant.get("sku") or "").strip() or None,
                    "title": str(variant.get("title") or "").strip() or "Default",
                    "price_amount": (
                        Decimal(str(variant.get("price")))
                        if variant.get("price") is not None and str(variant.get("price")).strip() != ""
                        else None
                    ),
                    "compare_at_price_amount": (
                        Decimal(str(variant.get("compare_at_price")))
                        if variant.get("compare_at_price") is not None and str(variant.get("compare_at_price")).strip() != ""
                        else None
                    ),
                    "currency_code": str(variant.get("currency") or "").strip().upper() or None,
                    "is_orderable": bool(variant.get("available", True)),
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

    def apply_batch(self, *, source_id: int, items: list[dict], reconcile_missing: bool = False) -> BatchApplyResult:
        result = BatchApplyResult(listings_seen=0, listings_applied=0)
        seen_listing_ids: set[int] = set()

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
                    handle=str(item.get("handle") or "").strip() or None,
                    source_title=str(item.get("title") or "").strip() or url,
                    source_description_html=str(item.get("description_html") or "").strip() or None,
                    source_description_text=str(item.get("description") or "").strip() or None,
                    source_weight_grams=self._positive_int(item.get("weight_grams")),
                    source_designer_raw=str(item.get("designer") or "").strip() or None,
                    source_category_raw=str(item.get("category") or "").strip() or None,
                    ingest_mode="sync",
                    last_seen_at=self._utcnow(),
                    last_synced_at=self._utcnow(),
                )
            else:
                listing.external_id = external_id
                listing.url = url
                listing.handle = str(item.get("handle") or "").strip() or None
                listing.source_title = str(item.get("title") or "").strip() or url
                listing.source_description_html = str(item.get("description_html") or "").strip() or None
                listing.source_description_text = str(item.get("description") or "").strip() or None
                listing.source_weight_grams = self._positive_int(item.get("weight_grams"))
                listing.source_designer_raw = str(item.get("designer") or "").strip() or None
                listing.source_category_raw = str(item.get("category") or "").strip() or None
                listing.last_seen_at = self._utcnow()
                listing.last_synced_at = self._utcnow()

            product = self.products.get_product_by_listing(int(listing.id))
            if product is None:
                product = self.products.create_product(
                    gender=self._normalize_gender(item.get("gender")),
                    availability_mode="by_order",
                    lifecycle_status="active",
                    visibility_status="visible",
                )
                self.products.ensure_membership(product_id=int(product.id), listing_id=int(listing.id))
                product.primary_listing_id = int(listing.id)

            incoming_status = self._normalize_orderability(str(item.get("status") or "unavailable"))
            incoming_reason = str(item.get("status_reason") or "").strip() or None
            listing.orderability_status, listing.status_reason = self._resolve_listing_status(
                product=product,
                listing=listing,
                incoming_status=incoming_status,
                incoming_reason=incoming_reason,
            )

            variants = self._variant_payloads(item)
            self.products.replace_variants(listing_id=int(listing.id), variants=variants)
            listing_images = self.products.replace_listing_images(listing_id=int(listing.id), image_urls=self._image_urls(item))
            self.products.replace_gallery_scope_with_source_images(
                product_id=int(product.id),
                listing_id=int(listing.id),
                listing_images=listing_images,
            )

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
                siblings = [
                    member_listing
                    for member_listing in self.products.list_product_listings(int(product.id))
                    if int(member_listing.id) != int(listing.id)
                ]
                if siblings:
                    product.primary_listing_id = int(siblings[0].id)

        self.db.flush()
        return result
