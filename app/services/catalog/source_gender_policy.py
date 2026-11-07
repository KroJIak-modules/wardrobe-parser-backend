from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy import String, case, cast, literal, select

from app.models import Product, ProductListing, Source


_ALLOWED = {"male", "female", "unisex"}


def _config_path() -> Path:
    container_path = Path(__file__).resolve().parents[3] / "shared-config" / "sources.json"
    if container_path.exists():
        return container_path
    return Path(__file__).resolve().parents[4] / "service" / "config" / "sources.json"


@lru_cache(maxsize=1)
def source_gender_overrides() -> dict[str, str]:
    payload = json.loads(_config_path().read_text(encoding="utf-8"))
    result: dict[str, str] = {}
    for source in payload.get("sources", []) if isinstance(payload, dict) else []:
        if not isinstance(source, dict):
            continue
        key = str(source.get("key") or "").strip().lower()
        config = source.get("config") if isinstance(source.get("config"), dict) else {}
        value = str(config.get("gender_override") or "").strip().lower()
        if key and value in _ALLOWED:
            result[key] = value
    return result


def effective_gender_for_product(product: Product) -> str:
    listing = getattr(product, "primary_listing", None)
    if listing is None:
        memberships = getattr(product, "memberships", []) or []
        listing = next(
            (getattr(member, "listing", None) for member in memberships if getattr(member, "listing", None) is not None),
            None,
        )
    source = getattr(listing, "source", None) if listing is not None else None
    key = str(getattr(source, "key", "") or "").strip().lower()
    return source_gender_overrides().get(key, str(getattr(product, "gender", "") or "unisex"))


def effective_gender_expression(*, product=Product, source_key_expr=None):
    overrides = source_gender_overrides()
    if source_key_expr is not None:
        return case(
            *[
                (source_key_expr.in_([key for key, value in overrides.items() if value == gender]), literal(gender))
                for gender in sorted(_ALLOWED)
                if any(value == gender for value in overrides.values())
            ],
            else_=cast(product.gender, String(16)),
        )
    branches = []
    for gender in sorted(_ALLOWED):
        keys = [key for key, value in overrides.items() if value == gender]
        if not keys:
            continue
        listing_ids = (
            select(ProductListing.id)
            .join(Source, Source.id == ProductListing.source_id)
            .where(Source.key.in_(keys))
        )
        branches.append((product.primary_listing_id.in_(listing_ids), literal(gender)))
    return case(*branches, else_=cast(product.gender, String(16)))
