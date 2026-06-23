"""Service for weight rules CRUD and keyword-based weight estimation."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Product, ProductListing, Source
from app.repositories import CatalogWeightRuleRepository
from app.schemas.admin_settings import (
    WeightMissingProductResponse,
    WeightRuleCreateRequest,
    WeightRuleKeywordRequest,
    WeightRuleResponse,
    WeightRuleUpdateRequest,
)
from app.services.settings.weight_rule_matcher import (
    WeightRuleMatcherField,
    WeightRuleMatcherEntry,
    keyword_has_wildcards,
    normalize_haystack,
    normalize_keyword,
    resolve_match,
    resolve_match_for_fields,
)
from app.services.settings.weight_recalc_queue import WeightRuleRecalcQueue


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class WeightMatchResult:
    rule_id: int | None
    weight_grams: float | None
    matched_keyword: str | None


def _normalize_keyword(keyword: str) -> str:
    normalized = normalize_keyword(keyword)
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ключевое слово не может быть пустым")
    if len(normalized) > 255:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ключевое слово слишком длинное")
    return normalized


def _normalize_match_haystack(*parts: str | None) -> str:
    return normalize_haystack(*parts)


def _keyword_to_sql_like_pattern(keyword: str) -> str:
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if keyword_has_wildcards(escaped):
        return escaped.replace("*", "%").replace("?", "_")
    return f"%{escaped}%"


def _unique_normalized_keywords(keywords: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        normalized = _normalize_keyword(keyword)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


DEFAULT_WEIGHT_RULES: list[tuple[int, list[str]]] = [
    (80, ["ring", "rings", "earring", "earrings", "brooch", "pin", "cufflink", "cufflinks", "ear cuff", "tie clip", "charm", "jewelry"]),
    (130, ["necklace", "chain", "pendant", "bracelet", "anklet", "bangle", "body chain", "wallet chain", "key chain", "keychain"]),
    (190, ["wallet", "wallets", "card holder", "cardholders", "card wallet", "passport holder", "coin purse", "coin pouch", "pouch", "glasses case", "sunglasses", "glasses", "eyewear", "gift card"]),
    (240, ["cap", "beanie", "bucket hat", "scarf", "belt", "gloves", "mittens", "tie", "mask", "headband", "hair clip", "barrette", "perfume", "fragrance", "towel", "book", "magazine"]),
    (300, ["tank top", "cami", "camisole", "bodysuit", "corset", "bralette", "bra", "bikini", "swimsuit", "tube top"]),
    (360, ["t shirt", "tee", "graphic tee", "jersey tee", "thermal tee", "top", "tops", "long sleeve tee", "polo tee", "raglan tee", "rib tee"]),
    (440, ["shirt", "shirts", "polo shirt", "blouse", "button up", "button down", "dress shirt", "denim shirt", "flannel shirt", "oxford shirt", "tunic"]),
    (540, ["shorts", "short", "skirt", "mini skirt", "midi skirt", "maxi skirt", "skort", "bermuda", "cargo shorts", "denim shorts", "jorts"]),
    (680, ["pants", "pant", "trousers", "trouser", "jeans", "joggers", "leggings", "sweatpants", "cargo pants", "track pants", "chinos", "slacks", "bottom", "bottoms", "culottes"]),
    (700, ["dress", "dresses", "maxi dress", "midi dress", "mini dress", "slip dress", "gown", "jumpsuit", "romper", "playsuit", "leotard", "catsuit", "overall", "overalls"]),
    (820, ["sweatshirt", "sweater", "cardigan", "jumper", "knit", "knitwear", "crewneck", "pullover", "zip sweater", "turtleneck"]),
    (960, ["hoodie", "zip hoodie", "hooded sweatshirt", "vest", "down vest", "puffer vest", "waistcoat", "gilet", "quarter zip", "poncho"]),
    (1120, ["jacket", "blazer", "outerwear", "denim jacket", "rain jacket", "shell jacket", "bomber", "windbreaker", "overshirt", "varsity jacket", "trucker jacket", "blouson", "fleece", "bolero"]),
    (1380, ["coat", "parka", "puffer", "puffer jacket", "down jacket", "overcoat", "trench coat", "duffle coat", "pea coat", "wool coat", "fur coat", "mouton"]),
    (1560, ["sneakers", "sneaker", "shoes", "shoe", "loafers", "loafer", "sandals", "sandal", "running shoes", "running shoe", "trainers", "derby", "derbies", "oxford shoes", "mule", "moccasin", "flats", "ballet flats", "heels", "heel", "pumps", "pump", "slippers", "slipper", "slides", "slide", "wedge", "platform shoes", "runners"]),
    (1860, ["boots", "boot", "ankle boots", "chelsea boots", "combat boots", "cowboy boots", "hiking boots", "platform boots"]),
    (2300, ["bag", "bags", "tote bag", "crossbody bag", "shoulder bag", "handbag", "messenger bag", "satchel", "sling bag", "waist bag", "belt bag", "fanny pack", "hobo bag", "clutch bag", "clutch", "pochette", "messenger"]),
    (2850, ["backpack", "duffle bag", "duffel bag", "rucksack", "travel bag", "weekender bag", "gym bag", "garment bag"]),
    (3800, ["suitcase", "carry on", "hard case luggage", "trolley case", "trunk case"]),
]


def _default_rules_unique() -> list[tuple[int, list[str]]]:
    by_weight: dict[int, list[str]] = {}
    for weight_grams, keywords in DEFAULT_WEIGHT_RULES:
        bucket = by_weight.setdefault(weight_grams, [])
        bucket.extend(keywords)
    return [(weight_grams, _unique_normalized_keywords(keywords)) for weight_grams, keywords in sorted(by_weight.items(), key=lambda row: row[0])]


class WeightRuleService:
    def __init__(self, db: Session):
        self.db = db
        self.rule_repo = CatalogWeightRuleRepository(db)

    def _build_responses(self) -> list[WeightRuleResponse]:
        rows: list[WeightRuleResponse] = []
        for rule in self.rule_repo.list_active():
            keywords = [item.keyword for item in self.rule_repo.list_keywords(int(rule.id))]
            rows.append(WeightRuleResponse(id=int(rule.id), weight_grams=int(rule.weight_grams), keywords=keywords))
        return rows

    def _normalize_rule_keywords(self, rule_id: int) -> bool:
        changed = False
        seen_normalized: set[str] = set()
        for item in self.rule_repo.list_keywords(rule_id):
            normalized = _normalize_keyword(item.keyword)
            if normalized in seen_normalized:
                self.db.delete(item)
                changed = True
                continue
            seen_normalized.add(normalized)
            if item.keyword != normalized:
                item.keyword = normalized
                changed = True
        return changed

    def ensure_default_rules(self) -> None:
        active = self.rule_repo.list_active()
        changed = False
        if not active:
            for weight_grams, keywords in _default_rules_unique():
                created = self.rule_repo.create_rule(weight_grams=weight_grams, is_enabled=True)
                for normalized in keywords:
                    self.rule_repo.create_keyword(rule_id=int(created.id), keyword=normalized)
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
            return

        for rule in active:
            changed = self._normalize_rule_keywords(int(rule.id)) or changed

        active = self.rule_repo.list_active()
        by_weight = {int(rule.weight_grams): rule for rule in active}
        for weight_grams, keywords in _default_rules_unique():
            rule = by_weight.get(weight_grams)
            if rule is None:
                rule = self.rule_repo.create_rule(weight_grams=weight_grams, is_enabled=True)
                by_weight[weight_grams] = rule
                changed = True

            existing = {item.keyword for item in self.rule_repo.list_keywords(int(rule.id))}
            for normalized in keywords:
                if normalized not in existing:
                    self.rule_repo.create_keyword(rule_id=int(rule.id), keyword=normalized)
                    existing.add(normalized)
                    changed = True

        if changed:
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()

    @staticmethod
    def _listing_match_fields(listing: ProductListing | None) -> list[WeightRuleMatcherField]:
        if listing is None:
            return []
        return [
            WeightRuleMatcherField(name="title", text=listing.source_title, weight=8),
            WeightRuleMatcherField(name="category", text=listing.source_category_raw, weight=7),
            WeightRuleMatcherField(name="handle", text=listing.handle, weight=6),
            WeightRuleMatcherField(name="designer", text=listing.source_designer_raw, weight=2),
            WeightRuleMatcherField(name="description_text", text=listing.source_description_text, weight=1),
            WeightRuleMatcherField(name="description_html", text=listing.source_description_html, weight=1),
        ]

    @staticmethod
    def _derive_listing_orderability(listing: ProductListing | None) -> str:
        if listing is None:
            return "unavailable"
        variants = list(listing.variants or [])
        if any(bool(variant.is_orderable) for variant in variants):
            return "orderable"
        if variants:
            return "sold_out"
        current = str(listing.orderability_status or "").strip().lower()
        return current if current in {"orderable", "sold_out", "unavailable"} else "unavailable"

    def _resolve_rule_match(self, listing: ProductListing | None, rules: list[WeightRuleResponse]) -> WeightMatchResult:
        fields = self._listing_match_fields(listing)
        if not fields or not rules:
            return WeightMatchResult(rule_id=None, weight_grams=None, matched_keyword=None)
        match = resolve_match_for_fields(
            rules=[
                WeightRuleMatcherEntry(
                    rule_id=int(rule.id),
                    weight_grams=int(rule.weight_grams),
                    keywords=[_normalize_keyword(keyword) for keyword in rule.keywords],
                )
                for rule in rules
            ],
            fields=fields,
        )
        return WeightMatchResult(rule_id=match.rule_id, weight_grams=match.weight_grams, matched_keyword=match.matched_keyword)

    def _find_candidate_product_ids(self, keywords: list[str]) -> set[int]:
        normalized = [str(keyword).strip().lower() for keyword in keywords if str(keyword).strip()]
        if not normalized:
            return set()
        conditions = []
        for keyword in normalized:
            token = _keyword_to_sql_like_pattern(keyword)
            conditions.extend(
                [
                    func.lower(func.coalesce(ProductListing.source_title, "")).like(token, escape="\\"),
                    func.lower(func.coalesce(ProductListing.source_description_text, "")).like(token, escape="\\"),
                    func.lower(func.coalesce(ProductListing.source_description_html, "")).like(token, escape="\\"),
                    func.lower(func.coalesce(ProductListing.source_designer_raw, "")).like(token, escape="\\"),
                    func.lower(func.coalesce(ProductListing.source_category_raw, "")).like(token, escape="\\"),
                    func.lower(func.coalesce(ProductListing.handle, "")).like(token, escape="\\"),
                ]
            )
        rows = (
            self.db.query(Product.id)
            .join(Product.primary_listing)
            .filter(Product.lifecycle_status != "merged")
            .filter((Product.manual_weight_grams.is_(None)) | (Product.manual_weight_grams <= 0))
            .filter((ProductListing.source_weight_grams.is_(None)) | (ProductListing.source_weight_grams <= 0))
            .filter(or_(*conditions))
            .all()
        )
        return {int(row[0]) for row in rows}

    def _find_current_rule_product_ids(self, rule_id: int) -> set[int]:
        rows = self.db.query(Product.id).filter(Product.weight_rule_id == int(rule_id)).all()
        return {int(row[0]) for row in rows}

    def _recalculate_products_for_weight_rules(self, *, only_product_ids: set[int] | None = None) -> int:
        rules = self.list_rules()
        products = self.rule_repo.list_products_for_weight_recalc(product_ids=only_product_ids if only_product_ids else None)
        changed = False

        for product in products:
            listing = product.primary_listing
            if listing is None:
                continue

            manual_weight = getattr(product, "manual_weight_grams", None)
            if manual_weight is not None and int(manual_weight) > 0:
                if product.weight_rule_id is not None:
                    product.weight_rule_id = None
                    changed = True
                if str(listing.status_reason or "") == "missing_weight":
                    listing.orderability_status = self._derive_listing_orderability(listing)
                    listing.status_reason = None
                    changed = True
                continue

            source_weight = getattr(listing, "source_weight_grams", None)
            if source_weight is not None and int(source_weight) > 0:
                if product.weight_rule_id is not None:
                    product.weight_rule_id = None
                    changed = True
                if str(listing.status_reason or "") == "missing_weight":
                    listing.orderability_status = self._derive_listing_orderability(listing)
                    listing.status_reason = None
                    changed = True
                continue

            match = self._resolve_rule_match(listing, rules)
            if match.rule_id is not None:
                if product.weight_rule_id != int(match.rule_id):
                    product.weight_rule_id = int(match.rule_id)
                    changed = True
                if str(listing.status_reason or "") == "missing_weight":
                    listing.orderability_status = self._derive_listing_orderability(listing)
                    listing.status_reason = None
                    changed = True
                continue

            if product.weight_rule_id is not None:
                product.weight_rule_id = None
                changed = True
            if str(listing.orderability_status or "") != "unavailable" or str(listing.status_reason or "") != "missing_weight":
                listing.orderability_status = "unavailable"
                listing.status_reason = "missing_weight"
                changed = True

        if changed:
            self.db.commit()
        return len(products)

    def recalculate_product_ids(self, product_ids: set[int] | list[int] | tuple[int, ...]) -> int:
        normalized = {int(product_id) for product_id in product_ids if int(product_id) > 0}
        if not normalized:
            return 0
        return self._recalculate_products_for_weight_rules(only_product_ids=normalized)

    def _enqueue_recalculation(self, product_ids: set[int]) -> None:
        normalized = {int(product_id) for product_id in product_ids if int(product_id) > 0}
        if not normalized:
            return
        try:
            enqueued = WeightRuleRecalcQueue().enqueue_product_ids(normalized)
            LOGGER.info("Queued weight recalculation for %s products (%s newly enqueued)", len(normalized), enqueued)
            return
        except Exception:
            LOGGER.exception("Failed to enqueue weight recalculation, falling back to synchronous recalculation")
        self._recalculate_products_for_weight_rules(only_product_ids=normalized)

    def list_rules(self) -> list[WeightRuleResponse]:
        self.ensure_default_rules()
        return self._build_responses()

    def create_rule(self, payload: WeightRuleCreateRequest) -> WeightRuleResponse:
        self.ensure_default_rules()
        created = self.rule_repo.create_rule(weight_grams=payload.weight_grams, is_enabled=True)
        self.db.commit()
        return WeightRuleResponse(id=int(created.id), weight_grams=int(created.weight_grams), keywords=[])

    def update_rule(self, rule_id: int, payload: WeightRuleUpdateRequest) -> WeightRuleResponse:
        rule = self.rule_repo.get_by_id(rule_id)
        if rule is None or not bool(rule.is_enabled):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Правило веса не найдено")
        rule.weight_grams = int(payload.weight_grams)
        keywords = [item.keyword for item in self.rule_repo.list_keywords(rule_id)]
        affected_ids = self._find_candidate_product_ids(keywords) | self._find_current_rule_product_ids(rule_id)
        self.db.commit()
        self._enqueue_recalculation(affected_ids)
        return WeightRuleResponse(id=int(rule.id), weight_grams=int(rule.weight_grams), keywords=keywords)

    def delete_rule(self, rule_id: int) -> dict:
        rule = self.rule_repo.get_by_id(rule_id)
        if rule is None or not bool(rule.is_enabled):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Правило веса не найдено")
        keywords = [item.keyword for item in self.rule_repo.list_keywords(rule_id)]
        affected_ids = self._find_candidate_product_ids(keywords) | self._find_current_rule_product_ids(rule_id)
        for keyword in self.rule_repo.list_keywords(rule_id):
            self.db.delete(keyword)
        rule.is_enabled = False
        self.db.commit()
        self._enqueue_recalculation(affected_ids)
        return {"ok": True}

    def add_keyword(self, rule_id: int, payload: WeightRuleKeywordRequest) -> dict:
        rule = self.rule_repo.get_by_id(rule_id)
        if rule is None or not bool(rule.is_enabled):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Правило веса не найдено")
        keyword = _normalize_keyword(payload.keyword)
        if self.rule_repo.get_keyword(rule_id=rule_id, keyword=keyword) is not None:
            return {"ok": True, "keyword": keyword, "duplicated": True}
        self.rule_repo.create_keyword(rule_id=rule_id, keyword=keyword)
        self.db.commit()
        affected_ids = self._find_candidate_product_ids([keyword]) | self._find_current_rule_product_ids(rule_id)
        self._enqueue_recalculation(affected_ids)
        return {"ok": True, "keyword": keyword}

    def remove_keyword(self, rule_id: int, keyword: str) -> dict:
        rule = self.rule_repo.get_by_id(rule_id)
        if rule is None or not bool(rule.is_enabled):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Правило веса не найдено")
        normalized = _normalize_keyword(keyword)
        entity = self.rule_repo.get_keyword(rule_id=rule_id, keyword=normalized)
        if entity is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ключевое слово не найдено")
        affected_ids = self._find_candidate_product_ids([normalized]) | self._find_current_rule_product_ids(rule_id)
        self.db.delete(entity)
        self.db.commit()
        self._enqueue_recalculation(affected_ids)
        return {"ok": True}

    def get_matching_rules(self) -> list[WeightRuleResponse]:
        try:
            return self.list_rules()
        except IntegrityError:
            self.db.rollback()
            return self.list_rules()

    def list_missing_weight_products(self, limit: int = 500, offset: int = 0) -> list[WeightMissingProductResponse]:
        safe_limit = max(1, min(limit, 5000))
        safe_offset = max(0, int(offset))
        rows = (
            self.db.query(Product, ProductListing, Source)
            .join(Product.primary_listing)
            .join(Source, Source.id == ProductListing.source_id)
            .filter(Product.lifecycle_status != "merged")
            .filter((Product.manual_weight_grams.is_(None)) | (Product.manual_weight_grams <= 0))
            .filter((ProductListing.source_weight_grams.is_(None)) | (ProductListing.source_weight_grams <= 0))
            .filter(Product.weight_rule_id.is_(None))
            .order_by(Product.updated_at.desc(), Product.id.desc())
            .offset(safe_offset)
            .limit(safe_limit)
            .all()
        )
        return [
            WeightMissingProductResponse(
                id=int(product.id),
                title=str(listing.source_title or f"Product {int(product.id)}"),
                url=(str(listing.url) if str(listing.ingest_mode or "") != "manual" else ""),
                source_id=(int(source.id) if str(listing.ingest_mode or "") != "manual" else None),
                source_name=(str(source.name) if str(listing.ingest_mode or "") != "manual" else None),
            )
            for product, listing, source in rows
        ]

    @staticmethod
    def match_weight_from_rules(
        *,
        rules: list[WeightRuleResponse],
        title: str | None,
        designer: str | None,
        category: str | None,
        handle: str | None,
    ) -> WeightMatchResult:
        if not rules:
            return WeightMatchResult(rule_id=None, weight_grams=None, matched_keyword=None)
        fields = [
            WeightRuleMatcherField(name="title", text=title, weight=8),
            WeightRuleMatcherField(name="category", text=category, weight=7),
            WeightRuleMatcherField(name="handle", text=handle, weight=6),
            WeightRuleMatcherField(name="designer", text=designer, weight=2),
        ]
        if not _normalize_match_haystack(title, designer, category, handle):
            return WeightMatchResult(rule_id=None, weight_grams=None, matched_keyword=None)

        match = resolve_match_for_fields(
            rules=[
                WeightRuleMatcherEntry(
                    rule_id=int(rule.id),
                    weight_grams=int(rule.weight_grams),
                    keywords=[_normalize_keyword(keyword) for keyword in rule.keywords],
                )
                for rule in rules
            ],
            fields=fields,
        )
        return WeightMatchResult(rule_id=match.rule_id, weight_grams=match.weight_grams, matched_keyword=match.matched_keyword)

    def match_weight_by_keywords(
        self,
        *,
        title: str | None,
        designer: str | None,
        category: str | None,
        handle: str | None,
    ) -> WeightMatchResult:
        return self.match_weight_from_rules(
            rules=self.get_matching_rules(),
            title=title,
            designer=designer,
            category=category,
            handle=handle,
        )
