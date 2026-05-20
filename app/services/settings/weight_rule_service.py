"""Service for weight rules CRUD and keyword-based weight estimation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.models import ParserProduct, ParserProductOriginVariant, ParserSource, ParserWeightRule
from app.repositories import ParserWeightKeywordRepository, ParserWeightRuleRepository
from app.schemas.parser import (
    WeightMissingProductResponse,
    WeightRuleCreateRequest,
    WeightRuleKeywordRequest,
    WeightRuleResponse,
    WeightRuleUpdateRequest,
)


@dataclass(slots=True)
class WeightMatchResult:
    weight_grams: float | None
    matched_keyword: str | None


def _normalize_keyword(keyword: str) -> str:
    normalized = re.sub(r"[^a-z0-9\s]+", " ", keyword.strip().lower())
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


def _default_rules_unique() -> list[tuple[int, list[str]]]:
    by_weight: dict[int, list[str]] = {}
    for weight_grams, keywords in DEFAULT_WEIGHT_RULES:
        bucket = by_weight.setdefault(weight_grams, [])
        bucket.extend(keywords)
    return [(weight_grams, _unique_normalized_keywords(keywords)) for weight_grams, keywords in sorted(by_weight.items(), key=lambda row: row[0])]


DEFAULT_WEIGHT_RULES: list[tuple[int, list[str]]] = [
    (
        80,
        [
            "ring",
            "rings",
            "earring",
            "earrings",
            "brooch",
            "pin badge",
            "pin",
            "lapel pin",
            "cufflink",
            "cufflinks",
            "ear cuff",
            "earcuff",
            "stud earring",
            "stud",
            "studs",
            "hoop earring",
            "hoop",
            "hoops",
            "ear hoop",
            "nose ring",
            "tie clip",
            "charm",
            "jewelry",
        ],
    ),
    (
        130,
        [
            "necklace",
            "chain",
            "pendant",
            "bracelet",
            "anklet",
            "bangle",
            "body chain",
            "wallet chain",
            "key chain",
            "keychain",
        ],
    ),
    (
        190,
        [
            "wallet",
            "wallets",
            "card holder",
            "cardholders",
            "card wallet",
            "passport holder",
            "coin purse",
            "coin pouch",
            "phone pouch",
            "mini pouch",
            "pouch",
            "glasses case",
            "sunglasses",
            "glasses",
            "eyewear",
            "aviator sunglasses",
            "cat eye sunglasses",
            "rectangular sunglasses",
            "oval sunglasses",
            "shield sunglasses",
            "gift card",
            "airpods case",
            "iphone case",
            "carabiner",
        ],
    ),
    (
        240,
        [
            "cap",
            "beanie",
            "bucket hat",
            "scarf",
            "belt",
            "gloves",
            "mittens",
            "tie",
            "necktie",
            "mask",
            "headband",
            "hair clip",
            "barrette",
            "tiara",
            "wristband",
            "snood",
            "neck gaiter",
            "leg warmers",
            "arm warmers",
            "lighter",
            "lighter holder",
            "candle",
            "candles",
            "perfume",
            "parfum",
            "eau de parfum",
            "fragrance",
            "room spray",
            "diffuser",
            "pot pourri",
            "refill perfume oil",
            "vase",
            "beach towel",
            "towel",
            "book",
            "magazine",
        ],
    ),
    (
        300,
        [
            "tank top",
            "cami",
            "camisole",
            "bodysuit",
            "corset",
            "corset top",
            "bra top",
            "bralette",
            "bra",
            "bikini",
            "swimsuit",
            "one piece swimsuit",
            "one piece",
            "tube top",
        ],
    ),
    (
        360,
        [
            "t shirt",
            "tee",
            "graphic tee",
            "jersey tee",
            "thermal tee",
            "top",
            "tops",
            "long sleeve tee",
            "polo tee",
            "raglan tee",
            "rib tee",
        ],
    ),
    (
        440,
        [
            "shirt",
            "shirts",
            "polo shirt",
            "blouse",
            "button up",
            "button down",
            "dress shirt",
            "denim shirt",
            "flannel shirt",
            "oxford shirt",
            "tunic",
        ],
    ),
    (
        540,
        [
            "shorts",
            "short",
            "skirt",
            "mini skirt",
            "midi skirt",
            "maxi skirt",
            "skort",
            "bermuda",
            "cargo shorts",
            "denim shorts",
            "boxer shorts",
            "jorts",
            "trunks",
        ],
    ),
    (
        680,
        [
            "pants",
            "pant",
            "trousers",
            "trouser",
            "jeans",
            "joggers",
            "leggings",
            "sweatpants",
            "cargo pants",
            "track pants",
            "capri pants",
            "chinos",
            "slacks",
            "bottom",
            "bottoms",
            "culottes",
        ],
    ),
    (
        700,
        [
            "dress",
            "dresses",
            "maxi dress",
            "midi dress",
            "mini dress",
            "slip dress",
            "cut out dress",
            "gown",
            "nightgown",
            "jumpsuit",
            "romper",
            "playsuit",
            "leotard",
            "catsuit",
            "stocking",
            "stockings",
            "tights",
            "stirrup tights",
            "bodice",
            "chemise",
            "bloomer",
            "knickers",
            "briefs",
            "boxers",
            "thong",
            "undie",
            "overall",
            "overalls",
        ],
    ),
    (
        820,
        [
            "sweatshirt",
            "sweater",
            "cardigan",
            "jumper",
            "knit",
            "knitwear",
            "crewneck",
            "pullover",
            "zip sweater",
            "turtleneck",
            "skivvy",
        ],
    ),
    (
        960,
        [
            "hoodie",
            "zip hoodie",
            "hooded sweatshirt",
            "vest",
            "down vest",
            "puffer vest",
            "waistcoat",
            "gilet",
            "quarter zip",
            "poncho",
            "arm sleeve",
            "sleeves",
        ],
    ),
    (
        1120,
        [
            "jacket",
            "blazer",
            "outerwear",
            "denim jacket",
            "rain jacket",
            "shell jacket",
            "bomber",
            "windbreaker",
            "overshirt",
            "varsity jacket",
            "trucker jacket",
            "blouson",
            "fleece",
            "bolero",
        ],
    ),
    (
        1380,
        [
            "coat",
            "parka",
            "puffer",
            "puffer jacket",
            "down jacket",
            "overcoat",
            "trench coat",
            "duffle coat",
            "pea coat",
            "wool coat",
            "fur coat",
            "mouton",
        ],
    ),
    (
        1560,
        [
            "sneakers",
            "sneaker",
            "shoes",
            "shoe",
            "loafers",
            "loafer",
            "sandals",
            "sandal",
            "running shoes",
            "running shoe",
            "trainers",
            "derby",
            "derbies",
            "oxford shoes",
            "mule",
            "moccasin",
            "flats",
            "ballet flats",
            "heels",
            "heel",
            "kitten heel",
            "pumps",
            "pump",
            "slippers",
            "slipper",
            "slides",
            "slide",
            "wedge",
            "platform shoes",
            "runners",
        ],
    ),
    (
        1860,
        [
            "boots",
            "boot",
            "ankle boots",
            "chelsea boots",
            "combat boots",
            "cowboy boots",
            "hiking boots",
            "platform boots",
            "prosthetic boots",
        ],
    ),
    (
        2300,
        [
            "bag",
            "bags",
            "tote bag",
            "crossbody bag",
            "shoulder bag",
            "handbag",
            "messenger bag",
            "satchel",
            "sling bag",
            "waist bag",
            "belt bag",
            "fanny pack",
            "hobo bag",
            "clutch bag",
            "clutch",
            "pochette",
            "messenger",
            "banane",
            "ceinture",
            "porte",
        ],
    ),
    (2850, ["backpack", "duffle bag", "duffel bag", "rucksack", "travel bag", "weekender bag", "gym bag", "garment bag"]),
    (3800, ["suitcase", "carry on", "hard case luggage", "trolley case", "trunk case"]),
]

DEFAULT_FALLBACK_WEIGHT_GRAMS = max(1, int(app_settings.weight_default_fallback_grams))
DEFAULT_FALLBACK_MATCH_KEYWORD = "fallback_default"


class WeightRuleService:
    def __init__(self, db: Session):
        self.db = db
        self.rule_repo = ParserWeightRuleRepository(db)
        self.keyword_repo = ParserWeightKeywordRepository(db)

    def _build_responses(self, rules: list[ParserWeightRule]) -> list[WeightRuleResponse]:
        rows: list[WeightRuleResponse] = []
        for rule in rules:
            keywords = [item.keyword for item in self.keyword_repo.get_by_rule(rule.id)]
            rows.append(WeightRuleResponse(id=rule.id, weight_grams=rule.weight_grams, keywords=keywords))
        return rows

    def _normalize_rule_keywords(self, rule_id: int) -> bool:
        changed = False
        seen_normalized: set[str] = set()
        for item in self.keyword_repo.get_by_rule(rule_id):
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

    def ensure_default_rules(self) -> list[ParserWeightRule]:
        active = self.rule_repo.get_all_active()
        changed = False
        if not active:
            for index, (weight_grams, keywords) in enumerate(_default_rules_unique(), start=1):
                created = self.rule_repo.create(weight_grams=weight_grams, sort_order=index)
                self.rule_repo.flush()
                for normalized in keywords:
                    self.keyword_repo.create(rule_id=created.id, keyword=normalized)
            try:
                self.db.commit()
            except IntegrityError:
                # Another transaction could seed defaults in parallel.
                self.db.rollback()
            return self.rule_repo.get_all_active()

        for rule in active:
            changed = self._normalize_rule_keywords(rule.id) or changed

        active = self.rule_repo.get_all_active()
        by_weight = {rule.weight_grams: rule for rule in active}
        next_sort_order = max((rule.sort_order for rule in active), default=0)
        for weight_grams, keywords in _default_rules_unique():
            rule = by_weight.get(weight_grams)
            if rule is None:
                next_sort_order += 1
                rule = self.rule_repo.create(weight_grams=weight_grams, sort_order=next_sort_order)
                self.rule_repo.flush()
                by_weight[weight_grams] = rule
                changed = True

            existing = {item.keyword for item in self.keyword_repo.get_by_rule(rule.id)}
            for normalized in keywords:
                if normalized not in existing:
                    self.keyword_repo.create(rule_id=rule.id, keyword=normalized)
                    existing.add(normalized)
                    changed = True

        if changed:
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
        return self.rule_repo.get_all_active()

    def list_rules(self) -> list[WeightRuleResponse]:
        return self._build_responses(self.ensure_default_rules())

    @staticmethod
    def _derive_status_from_variants(variants: list[dict] | None) -> str:
        values = variants if isinstance(variants, list) else []
        if not values:
            return "available"
        any_available = any(bool(item.get("available")) for item in values if isinstance(item, dict))
        return "available" if any_available else "out_of_stock"

    def _recalculate_products_for_weight_rules(self, *, only_product_ids: set[int] | None = None) -> None:
        rules = self.get_matching_rules()
        managed_sources = {"missing", "keyword_rule", "fallback_rule", ""}
        managed_statuses = {"available", "out_of_stock", "unavailable"}
        query = (
            self.db.query(ParserProduct)
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(
                (ParserProduct.weight_source.is_(None))
                | (ParserProduct.weight_source.in_(("missing", "keyword_rule", "fallback_rule")))
                | (ParserProduct.status == "unavailable")
            )
        )
        if only_product_ids:
            query = query.filter(ParserProduct.id.in_(tuple(sorted(only_product_ids))))
        rows = query.all()
        changed = False
        for product in rows:
            source_raw = str(getattr(product, "weight_source", "") or "").strip().lower()
            status_raw = str(getattr(product, "status", "") or "").strip().lower()
            if status_raw not in managed_statuses:
                continue
            if source_raw not in managed_sources:
                # Keep parser/source-provided weights untouched.
                continue
            match = self.match_weight_from_rules(
                rules=rules,
                title=getattr(product, "title", None),
                vendor=getattr(product, "vendor", None),
                product_type=getattr(product, "product_type", None),
                handle=getattr(product, "handle", None),
                allow_fallback=False,
            )
            if match.weight_grams is None or match.weight_grams <= 0:
                if product.weight_grams is not None or source_raw != "missing" or product.weight_match_keyword is not None:
                    product.weight_grams = None
                    product.weight_source = "missing"
                    product.weight_match_keyword = None
                    changed = True
                if status_raw != "hidden" and status_raw != "unavailable":
                    product.status = "unavailable"
                    changed = True
                continue

            next_source = "fallback_rule" if match.matched_keyword == DEFAULT_FALLBACK_MATCH_KEYWORD else "keyword_rule"
            if product.weight_grams != float(match.weight_grams):
                product.weight_grams = float(match.weight_grams)
                changed = True
            if str(product.weight_source or "") != next_source:
                product.weight_source = next_source
                changed = True
            next_keyword = None if match.matched_keyword == DEFAULT_FALLBACK_MATCH_KEYWORD else match.matched_keyword
            if str(product.weight_match_keyword or "") != str(next_keyword or ""):
                product.weight_match_keyword = next_keyword
                changed = True
            if status_raw == "unavailable":
                product.status = self._derive_status_from_variants(getattr(product, "variants", None))
                changed = True

        if changed:
            self.db.commit()

    def _find_product_ids_by_keywords(self, keywords: list[str]) -> set[int]:
        normalized = [k.strip().lower() for k in keywords if k and k.strip()]
        if not normalized:
            return set()
        q = self.db.query(ParserProduct.id).filter(ParserProduct.deleted_at.is_(None))
        conditions = []
        for kw in normalized:
            token = f"%{kw}%"
            conditions.extend(
                [
                    func.lower(func.coalesce(ParserProduct.title, "")).like(token),
                    func.lower(func.coalesce(ParserProduct.vendor, "")).like(token),
                    func.lower(func.coalesce(ParserProduct.product_type, "")).like(token),
                    func.lower(func.coalesce(ParserProduct.handle, "")).like(token),
                ]
            )
        q = q.filter(or_(*conditions))
        return {int(row[0]) for row in q.all()}

    def create_rule(self, payload: WeightRuleCreateRequest) -> WeightRuleResponse:
        self.ensure_default_rules()
        current = self.rule_repo.get_all_active()
        created = self.rule_repo.create(weight_grams=payload.weight_grams, sort_order=max((item.sort_order for item in current), default=0) + 1)
        self.rule_repo.flush()
        self.db.commit()
        self._recalculate_products_for_weight_rules()
        return WeightRuleResponse(id=created.id, weight_grams=created.weight_grams, keywords=[])

    def update_rule(self, rule_id: int, payload: WeightRuleUpdateRequest) -> WeightRuleResponse:
        rule = self.rule_repo.get_by_id(rule_id)
        if not rule or rule.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Правило веса не найдено")
        rule.weight_grams = payload.weight_grams
        self.db.commit()
        keywords = [item.keyword for item in self.keyword_repo.get_by_rule(rule.id)]
        affected_ids = self._find_product_ids_by_keywords(keywords)
        self._recalculate_products_for_weight_rules(only_product_ids=affected_ids)
        return WeightRuleResponse(id=rule.id, weight_grams=rule.weight_grams, keywords=keywords)

    def delete_rule(self, rule_id: int) -> dict:
        rule = self.rule_repo.get_by_id(rule_id)
        if not rule or rule.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Правило веса не найдено")
        existing_keywords = [item.keyword for item in self.keyword_repo.get_by_rule(rule_id)]
        affected_ids = self._find_product_ids_by_keywords(existing_keywords)
        for keyword in self.keyword_repo.get_by_rule(rule_id):
            self.db.delete(keyword)
        rule.deleted_at = datetime.now(timezone.utc)
        self.db.commit()
        self._recalculate_products_for_weight_rules(only_product_ids=affected_ids)
        return {"ok": True}

    def add_keyword(self, rule_id: int, payload: WeightRuleKeywordRequest) -> dict:
        rule = self.rule_repo.get_by_id(rule_id)
        if not rule or rule.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Правило веса не найдено")
        keyword = _normalize_keyword(payload.keyword)
        if self.keyword_repo.get_exact(rule_id, keyword):
            return {"ok": True, "keyword": keyword, "duplicated": True}
        self.keyword_repo.create(rule_id=rule_id, keyword=keyword)
        self.db.commit()
        affected_ids = self._find_product_ids_by_keywords([keyword])
        self._recalculate_products_for_weight_rules(only_product_ids=affected_ids)
        return {"ok": True, "keyword": keyword}

    def remove_keyword(self, rule_id: int, keyword: str) -> dict:
        rule = self.rule_repo.get_by_id(rule_id)
        if not rule or rule.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Правило веса не найдено")
        normalized = _normalize_keyword(keyword)
        entity = self.keyword_repo.get_exact(rule_id, normalized)
        if not entity:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ключевое слово не найдено")
        affected_ids = self._find_product_ids_by_keywords([normalized])
        # Also include products that were explicitly matched by this keyword.
        matched_ids = {
            int(row[0])
            for row in self.db.query(ParserProduct.id)
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(func.lower(func.coalesce(ParserProduct.weight_match_keyword, "")) == normalized)
            .all()
        }
        affected_ids.update(matched_ids)
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
        origin_source_subq = (
            self.db.query(
                ParserProductOriginVariant.product_id.label("product_id"),
                ParserProductOriginVariant.source_id.label("source_id"),
            )
            .distinct(ParserProductOriginVariant.product_id)
            .order_by(ParserProductOriginVariant.product_id.asc(), ParserProductOriginVariant.id.asc())
            .subquery()
        )
        rows = (
            self.db.query(ParserProduct, ParserSource)
            .join(origin_source_subq, origin_source_subq.c.product_id == ParserProduct.id)
            .join(ParserSource, ParserSource.id == origin_source_subq.c.source_id)
            .filter(ParserProduct.deleted_at.is_(None))
            .filter((ParserProduct.weight_grams.is_(None)) | (ParserProduct.weight_source == "missing"))
            .order_by(ParserProduct.updated_at.desc())
            .offset(safe_offset)
            .limit(safe_limit)
            .all()
        )
        unresolved: list[WeightMissingProductResponse] = []
        for product, source in rows:
            unresolved.append(
                WeightMissingProductResponse(
                    id=product.id,
                    title=product.title,
                    url=product.url,
                    source_id=source.id,
                    source_name=source.name,
                )
            )
        return unresolved

    @staticmethod
    def _resolve_fallback_weight(rules: list[WeightRuleResponse]) -> float | None:
        if not rules:
            return None
        weights = sorted({int(rule.weight_grams) for rule in rules if int(rule.weight_grams) > 0})
        if not weights:
            return None
        if DEFAULT_FALLBACK_WEIGHT_GRAMS in weights:
            return float(DEFAULT_FALLBACK_WEIGHT_GRAMS)
        return float(weights[len(weights) // 2])

    @staticmethod
    def match_weight_from_rules(
        *,
        rules: list[WeightRuleResponse],
        title: str | None,
        vendor: str | None,
        product_type: str | None,
        handle: str | None,
        allow_fallback: bool = True,
    ) -> WeightMatchResult:
        if not rules:
            return WeightMatchResult(weight_grams=None, matched_keyword=None)
        haystack = _normalize_match_haystack(title, vendor, product_type, handle)
        if not haystack:
            if not allow_fallback:
                return WeightMatchResult(weight_grams=None, matched_keyword=None)
            fallback_weight = WeightRuleService._resolve_fallback_weight(rules)
            if fallback_weight is None:
                return WeightMatchResult(weight_grams=None, matched_keyword=None)
            return WeightMatchResult(weight_grams=fallback_weight, matched_keyword=DEFAULT_FALLBACK_MATCH_KEYWORD)

        best_rule_weight: int | None = None
        best_keyword: str | None = None
        best_keyword_len = -1
        for rule in rules:
            for keyword in rule.keywords:
                normalized_keyword = _normalize_keyword(keyword)
                if normalized_keyword in haystack:
                    keyword_len = len(normalized_keyword)
                    if keyword_len > best_keyword_len or (
                        keyword_len == best_keyword_len and (best_rule_weight is None or rule.weight_grams > best_rule_weight)
                    ):
                        best_keyword_len = keyword_len
                        best_rule_weight = rule.weight_grams
                        best_keyword = normalized_keyword

        if best_rule_weight is None:
            if not allow_fallback:
                return WeightMatchResult(weight_grams=None, matched_keyword=None)
            fallback_weight = WeightRuleService._resolve_fallback_weight(rules)
            if fallback_weight is None:
                return WeightMatchResult(weight_grams=None, matched_keyword=None)
            return WeightMatchResult(weight_grams=fallback_weight, matched_keyword=DEFAULT_FALLBACK_MATCH_KEYWORD)
        return WeightMatchResult(weight_grams=float(best_rule_weight), matched_keyword=best_keyword)

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
