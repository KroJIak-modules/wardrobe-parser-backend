from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


_ALNUM_CLASS = r"[a-z0-9]"


@dataclass(frozen=True, slots=True)
class WeightRuleMatcherEntry:
    rule_id: int
    weight_grams: int
    keywords: list[str]


@dataclass(frozen=True, slots=True)
class WeightRuleMatch:
    rule_id: int | None
    weight_grams: float | None
    matched_keyword: str | None


@dataclass(frozen=True, slots=True)
class WeightRuleMatcherField:
    name: str
    text: str | None
    weight: int


def normalize_keyword(keyword: str) -> str:
    normalized = re.sub(r"[^a-z0-9\s*?]+", " ", str(keyword or "").strip().lower())
    return " ".join(normalized.split())


def normalize_haystack(*parts: str | None) -> str:
    text = " ".join(str(item or "").strip().lower() for item in parts if str(item or "").strip())
    normalized = re.sub(r"[^a-z0-9\s]+", " ", text)
    return " ".join(normalized.split())


def keyword_has_wildcards(keyword: str) -> bool:
    return "*" in keyword or "?" in keyword


def keyword_specificity(keyword: str) -> int:
    return len(keyword.replace("*", "").replace("?", "").strip())


def keyword_matches(*, haystack: str, keyword: str) -> bool:
    normalized_keyword = normalize_keyword(keyword)
    if not normalized_keyword or not haystack:
        return False
    return bool(keyword_pattern(normalized_keyword).search(haystack))


def keyword_pattern(keyword: str) -> re.Pattern[str]:
    normalized = normalize_keyword(keyword)
    chunks: list[str] = [rf"(?<!{_ALNUM_CLASS})"]
    for char in normalized:
        if char == " ":
            chunks.append(r"\s+")
        elif char == "*":
            chunks.append(rf"{_ALNUM_CLASS}*")
        elif char == "?":
            chunks.append(_ALNUM_CLASS)
        else:
            chunks.append(re.escape(char))
    chunks.append(rf"(?!{_ALNUM_CLASS})")
    return re.compile("".join(chunks), flags=re.IGNORECASE)


def resolve_match_for_fields(*, rules: Iterable[WeightRuleMatcherEntry], fields: Iterable[WeightRuleMatcherField]) -> WeightRuleMatch:
    normalized_fields: list[WeightRuleMatcherField] = []
    for field in fields:
        normalized_text = normalize_haystack(field.text)
        if not normalized_text:
            continue
        normalized_fields.append(WeightRuleMatcherField(name=field.name, text=normalized_text, weight=max(1, int(field.weight))))
    if not normalized_fields:
        return WeightRuleMatch(rule_id=None, weight_grams=None, matched_keyword=None)

    best_score = 0
    best_hits = 0
    best_rule_id: int | None = None
    best_rule_weight: int | None = None
    best_keyword: str | None = None
    best_keyword_len = -1

    for rule in rules:
        local_score = 0
        local_hits = 0
        local_best_keyword: str | None = None
        local_best_keyword_len = -1
        for keyword in rule.keywords:
            normalized_keyword = normalize_keyword(keyword)
            if not normalized_keyword:
                continue
            keyword_score = 0
            for field in normalized_fields:
                if keyword_matches(haystack=str(field.text or ""), keyword=normalized_keyword):
                    keyword_score += int(field.weight)
            if keyword_score <= 0:
                continue
            local_score += keyword_score
            local_hits += 1
            current_len = keyword_specificity(normalized_keyword)
            if current_len > local_best_keyword_len or (
                current_len == local_best_keyword_len and normalized_keyword < str(local_best_keyword or "\uffff")
            ):
                local_best_keyword = normalized_keyword
                local_best_keyword_len = current_len
        if local_hits <= 0 or local_score <= 0:
            continue
        if (
            local_score > best_score
            or (local_score == best_score and local_hits > best_hits)
            or (local_score == best_score and local_hits == best_hits and local_best_keyword_len > best_keyword_len)
            or (
                local_score == best_score
                and local_hits == best_hits
                and local_best_keyword_len == best_keyword_len
                and best_rule_id is not None
                and int(rule.rule_id) < best_rule_id
            )
        ):
            best_score = local_score
            best_hits = local_hits
            best_rule_id = int(rule.rule_id)
            best_rule_weight = int(rule.weight_grams)
            best_keyword = local_best_keyword
            best_keyword_len = local_best_keyword_len

    if best_rule_id is None or best_rule_weight is None:
        return WeightRuleMatch(rule_id=None, weight_grams=None, matched_keyword=None)
    return WeightRuleMatch(rule_id=best_rule_id, weight_grams=float(best_rule_weight), matched_keyword=best_keyword)


def resolve_match(*, rules: Iterable[WeightRuleMatcherEntry], haystack: str) -> WeightRuleMatch:
    return resolve_match_for_fields(
        rules=rules,
        fields=[WeightRuleMatcherField(name="haystack", text=haystack, weight=1)],
    )
