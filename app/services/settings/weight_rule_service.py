"""Service for weight rules CRUD and keyword-based weight estimation."""

from __future__ import annotations

from dataclasses import dataclass
import re

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Product, ProductListing, Source
from app.repositories import CatalogWeightRuleRepository
from app.schemas.parser import (
    WeightMissingProductResponse,
    WeightRuleCreateRequest,
    WeightRuleKeywordRequest,
    WeightRuleResponse,
    WeightRuleUpdateRequest,
)


@dataclass(slots=True)
class WeightMatchResult:
    rule_id: int | None
    weight_grams: float | None
    matched_keyword: str | None


def _normalize_keyword(keyword: str) -> str:
    normalized = re.sub(r"[^a-z0-9\s*?]+", " ", keyword.strip().lower())
    normalized = " ".join(normalized.split())
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ключевое слово не может быть пустым")
    if len(normalized) > 255:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ключевое слово слишком длинное")
    return normalized


def _normalize_match_haystack(*parts: str | None) -> str:
    text = " ".join(item.strip().lower() for item in parts if item and item.strip())
    normalized = re.sub(r"[^a-z0-9\s]+", " ", text)
    return " ".join(normalized.split())


def _keyword_has_wildcards(keyword: str) -> bool:
    return "*" in keyword or "?" in keyword


def _keyword_wildcard_to_regex(keyword: str) -> re.Pattern[str]:
    escaped = re.escape(keyword)
    pattern = escaped.replace(r"\*", ".*").replace(r"\?", ".")
    return re.compile(pattern, flags=re.IGNORECASE)


def _keyword_specificity(keyword: str) -> int:
    return len(keyword.replace("*", "").replace("?", "").strip())


def _keyword_to_sql_like_pattern(keyword: str) -> str:
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if _keyword_has_wildcards(escaped):
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
    def _listing_haystack(listing: ProductListing | None) -> str:
        if listing is None:
            return ""
        return " ".join(
            [
                str(listing.source_title or ""),
                str(listing.source_description_text or ""),
                str(listing.source_description_html or ""),
                str(listing.source_designer_raw or ""),
                str(listing.source_category_raw or ""),
            ]
        ).lower()

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
        haystack = self._listing_haystack(listing)
        if not haystack.strip() or not rules:
            return WeightMatchResult(rule_id=None, weight_grams=None, matched_keyword=None)

        best_hits = 0
        best_rule_id: int | None = None
        best_weight_grams: int | None = None
        best_keyword: str | None = None
        best_keyword_len = -1

        for rule in rules:
            local_hits = 0
            local_best_keyword: str | None = None
            local_best_keyword_len = -1
            for keyword in rule.keywords:
                normalized_keyword = _normalize_keyword(keyword)
                matched = bool(_keyword_wildcard_to_regex(normalized_keyword).search(haystack)) if _keyword_has_wildcards(normalized_keyword) else normalized_keyword in haystack
                if not matched:
                    continue
                local_hits += 1
                keyword_len = _keyword_specificity(normalized_keyword)
                if keyword_len > local_best_keyword_len:
                    local_best_keyword_len = keyword_len
                    local_best_keyword = normalized_keyword
            if local_hits <= 0:
                continue
            if (
                local_hits > best_hits
                or (local_hits == best_hits and local_best_keyword_len > best_keyword_len)
                or (
                    local_hits == best_hits
                    and local_best_keyword_len == best_keyword_len
                    and best_rule_id is not None
                    and int(rule.id) < best_rule_id
                )
            ):
                best_hits = local_hits
                best_rule_id = int(rule.id)
                best_weight_grams = int(rule.weight_grams)
                best_keyword = local_best_keyword
                best_keyword_len = local_best_keyword_len

        return WeightMatchResult(
            rule_id=best_rule_id,
            weight_grams=(float(best_weight_grams) if best_weight_grams is not None else None),
            matched_keyword=best_keyword,
        )

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

    def _recalculate_products_for_weight_rules(self, *, only_product_ids: set[int] | None = None) -> None:
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
        self._recalculate_products_for_weight_rules(only_product_ids=affected_ids)
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
        self._recalculate_products_for_weight_rules(only_product_ids=affected_ids)
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
        self._recalculate_products_for_weight_rules(only_product_ids=affected_ids)
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
        self._recalculate_products_for_weight_rules(only_product_ids=affected_ids)
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
        vendor: str | None,
        product_type: str | None,
        handle: str | None,
    ) -> WeightMatchResult:
        if not rules:
            return WeightMatchResult(rule_id=None, weight_grams=None, matched_keyword=None)
        haystack = _normalize_match_haystack(title, vendor, product_type, handle)
        if not haystack:
            return WeightMatchResult(rule_id=None, weight_grams=None, matched_keyword=None)

        best_hits = 0
        best_rule_id: int | None = None
        best_rule_weight: int | None = None
        best_keyword: str | None = None
        best_keyword_len = -1

        for rule in rules:
            local_hits = 0
            local_best_keyword: str | None = None
            local_best_keyword_len = -1
            for keyword in rule.keywords:
                normalized_keyword = _normalize_keyword(keyword)
                matched = bool(_keyword_wildcard_to_regex(normalized_keyword).search(haystack)) if _keyword_has_wildcards(normalized_keyword) else normalized_keyword in haystack
                if not matched:
                    continue
                local_hits += 1
                keyword_len = _keyword_specificity(normalized_keyword)
                if keyword_len > local_best_keyword_len:
                    local_best_keyword_len = keyword_len
                    local_best_keyword = normalized_keyword
            if local_hits <= 0:
                continue
            if (
                local_hits > best_hits
                or (local_hits == best_hits and local_best_keyword_len > best_keyword_len)
                or (
                    local_hits == best_hits
                    and local_best_keyword_len == best_keyword_len
                    and best_rule_id is not None
                    and int(rule.id) < best_rule_id
                )
            ):
                best_hits = local_hits
                best_rule_id = int(rule.id)
                best_rule_weight = int(rule.weight_grams)
                best_keyword = local_best_keyword
                best_keyword_len = local_best_keyword_len

        if best_rule_id is None or best_rule_weight is None:
            return WeightMatchResult(rule_id=None, weight_grams=None, matched_keyword=None)
        return WeightMatchResult(rule_id=best_rule_id, weight_grams=float(best_rule_weight), matched_keyword=best_keyword)

    def match_weight_by_keywords(
        self,
        *,
        title: str | None,
        vendor: str | None,
        product_type: str | None,
        handle: str | None,
    ) -> WeightMatchResult:
        return self.match_weight_from_rules(
            rules=self.get_matching_rules(),
            title=title,
            vendor=vendor,
            product_type=product_type,
            handle=handle,
        )
