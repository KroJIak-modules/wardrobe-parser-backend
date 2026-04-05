"""Scoring helpers for dedup candidate generation."""

from __future__ import annotations

from app.core.config import settings
from app.models import ParserProduct


def pair_key(a: int, b: int) -> str:
    left, right = sorted([a, b])
    return f"{left}:{right}"


def normalize_title(title: str) -> str:
    return " ".join(title.strip().lower().split())


def normalize_vendor(vendor: str | None) -> str:
    return (vendor or "").strip().lower()


def candidate_score(left: ParserProduct, right: ParserProduct) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0

    if normalize_title(left.title) == normalize_title(right.title):
        score += settings.dedup_title_match_weight
        reasons.append("title_match")

    left_vendor = normalize_vendor(left.vendor)
    right_vendor = normalize_vendor(right.vendor)
    if left_vendor and left_vendor == right_vendor:
        score += settings.dedup_vendor_match_weight
        reasons.append("vendor_match")

    if left.price is not None and right.price is not None:
        max_price = max(left.price, right.price)
        diff = abs(left.price - right.price)
        if max_price > 0 and diff / max_price <= settings.dedup_price_diff_ratio_limit:
            score += settings.dedup_price_close_weight
            reasons.append("price_close")

    if left.handle == right.handle:
        score += settings.dedup_handle_match_weight
        reasons.append("handle_match")

    return min(score, settings.dedup_score_cap), reasons
