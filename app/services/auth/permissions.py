"""Permission catalog and normalization helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionScope:
    key: str
    read: str
    edit: str


PERMISSION_SCOPES: tuple[PermissionScope, ...] = (
    PermissionScope(key="showcase", read="showcase.read", edit="showcase.edit"),
    PermissionScope(key="control.sources", read="control.sources.read", edit="control.sources.edit"),
    PermissionScope(key="control.products", read="control.products.read", edit="control.products.edit"),
    PermissionScope(key="control.dedup", read="control.dedup.read", edit="control.dedup.edit"),
    PermissionScope(key="control.categories", read="control.categories.read", edit="control.categories.edit"),
    PermissionScope(key="control.designers", read="control.designers.read", edit="control.designers.edit"),
    PermissionScope(key="control.pricing", read="control.pricing.read", edit="control.pricing.edit"),
    PermissionScope(key="control.weight", read="control.weight.read", edit="control.weight.edit"),
    PermissionScope(key="control.settings", read="control.settings.read", edit="control.settings.edit"),
    PermissionScope(key="accounts", read="accounts.read", edit="accounts.edit"),
)

ALL_PERMISSION_KEYS = {scope.read for scope in PERMISSION_SCOPES} | {scope.edit for scope in PERMISSION_SCOPES}


def normalize_permission_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = str(item or "").strip()
        if not key or key not in ALL_PERMISSION_KEYS or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized
