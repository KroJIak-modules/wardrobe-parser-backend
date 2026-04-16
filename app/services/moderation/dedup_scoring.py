"""Scoring and blocking helpers for dedup candidate generation."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

from app.core.config import settings
from app.models import ParserProduct

_NON_WORD_RE = re.compile(r"[^a-z0-9а-яё]+", re.IGNORECASE)
_MULTISPACE_RE = re.compile(r"\s+")


def pair_key(a: int, b: int) -> str:
    left, right = sorted([a, b])
    return f"{left}:{right}"


def normalize_text(value: str | None) -> str:
    cleaned = _NON_WORD_RE.sub(" ", str(value or "").strip().lower())
    return _MULTISPACE_RE.sub(" ", cleaned).strip()


def tokenize_text(value: str | None) -> list[str]:
    normalized = normalize_text(value)
    if not normalized:
        return []
    return [token for token in normalized.split(" ") if token]


def normalize_title(title: str | None) -> str:
    return normalize_text(title)


def normalize_vendor(vendor: str | None) -> str:
    return normalize_text(vendor)


def normalize_handle(handle: str | None) -> str:
    return normalize_text(handle)


def _token_jaccard(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    if not union:
        return 0.0
    return len(left_set & right_set) / len(union)


def _sequence_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _normalize_image_path(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or url
    return normalize_text(path)


def extract_image_fingerprints(product: ParserProduct) -> set[str]:
    fingerprints: set[str] = set()
    image_urls = product.image_urls if isinstance(product.image_urls, list) else []
    for raw_url in image_urls:
        normalized = _normalize_image_path(str(raw_url or ""))
        if normalized:
            fingerprints.add(normalized)
    return fingerprints


def extract_variant_fingerprints(product: ParserProduct) -> set[str]:
    variants = product.variants if isinstance(product.variants, list) else []
    result: set[str] = set()
    for raw_variant in variants:
        if not isinstance(raw_variant, dict):
            continue
        option_tokens: list[str] = []
        for key in ("title", "option1", "option2", "option3", "sku"):
            value = normalize_text(str(raw_variant.get(key) or ""))
            if value:
                option_tokens.append(value)
        if not option_tokens:
            continue
        result.add("|".join(option_tokens))
    return result


def build_candidate_keys(product: ParserProduct) -> set[str]:
    """Return lightweight blocking keys to avoid brute-force all-vs-all scans."""
    title_tokens = tokenize_text(product.title)
    vendor_key = normalize_vendor(product.vendor)
    handle_key = normalize_handle(product.handle)
    image_keys = sorted(extract_image_fingerprints(product))
    variant_keys = sorted(extract_variant_fingerprints(product))

    keys: set[str] = set()

    if vendor_key and title_tokens:
        keys.add(f"vendor_title:{vendor_key}:{' '.join(title_tokens[:2])}")

    if len(title_tokens) >= 3:
        keys.add(f"title3:{' '.join(title_tokens[:3])}")
    elif title_tokens:
        keys.add(f"title:{' '.join(title_tokens)}")

    if handle_key:
        keys.add(f"handle:{handle_key}")

    if vendor_key:
        keys.add(f"vendor:{vendor_key}")

    if image_keys:
        keys.add(f"image:{image_keys[0]}")

    if variant_keys:
        keys.add(f"variant:{variant_keys[0]}")

    return {key for key in keys if key}


def candidate_score(
    left: ParserProduct,
    right: ParserProduct,
    *,
    left_price: float | None = None,
    right_price: float | None = None,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0

    left_title = normalize_title(left.title)
    right_title = normalize_title(right.title)
    title_jaccard = _token_jaccard(tokenize_text(left_title), tokenize_text(right_title))
    title_seq = _sequence_similarity(left_title, right_title)
    title_similarity = max(title_jaccard, title_seq)
    if title_similarity >= 0.85:
        score += settings.dedup_title_match_weight
        reasons.append("title_match")
    elif title_similarity >= 0.70:
        score += settings.dedup_title_match_weight * 0.7
        reasons.append("title_similar")

    left_vendor = normalize_vendor(left.vendor)
    right_vendor = normalize_vendor(right.vendor)
    if left_vendor and left_vendor == right_vendor:
        score += settings.dedup_vendor_match_weight
        reasons.append("vendor_match")

    effective_left_price = left_price if left_price is not None else left.price
    effective_right_price = right_price if right_price is not None else right.price
    if effective_left_price is not None and effective_right_price is not None:
        max_price = max(effective_left_price, effective_right_price)
        diff = abs(effective_left_price - effective_right_price)
        if max_price > 0 and diff / max_price <= settings.dedup_price_diff_ratio_limit:
            score += settings.dedup_price_close_weight
            reasons.append("price_close")

    left_handle = normalize_handle(left.handle)
    right_handle = normalize_handle(right.handle)
    if left_handle and left_handle == right_handle:
        score += settings.dedup_handle_match_weight
        reasons.append("handle_match")
    else:
        handle_similarity = _sequence_similarity(left_handle, right_handle)
        if handle_similarity >= 0.88:
            score += settings.dedup_handle_match_weight * 0.5
            reasons.append("handle_similar")

    left_images = extract_image_fingerprints(left)
    right_images = extract_image_fingerprints(right)
    if left_images and right_images and (left_images & right_images):
        score += 0.20
        reasons.append("image_overlap")

    left_variants = extract_variant_fingerprints(left)
    right_variants = extract_variant_fingerprints(right)
    if left_variants and right_variants:
        overlap = _token_jaccard(sorted(left_variants), sorted(right_variants))
        if overlap >= 0.35:
            score += 0.20
            reasons.append("variant_overlap")

    return min(score, settings.dedup_score_cap), reasons
