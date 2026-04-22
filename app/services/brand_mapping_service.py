"""Brand mapping service for source->target vendor normalization."""

from __future__ import annotations

import unicodedata

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import ParserProduct
from app.repositories import ParserBrandMappingRepository


class BrandMappingService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = ParserBrandMappingRepository(db)

    @staticmethod
    def normalize_brand_key(value: str | None) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
        return "".join(ch for ch in normalized if ch.isalnum())

    @staticmethod
    def normalize_brand_name(value: str | None) -> str:
        return str(value or "").strip()

    def get_mapping_by_key(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for row in self.repo.list_all():
            if not bool(getattr(row, "include_in_designers", True)):
                continue
            key = self.normalize_brand_key(row.source_brand_key)
            target = self.normalize_brand_name(row.target_brand)
            if not key or not target:
                continue
            mapping[key] = target
        return mapping

    def get_excluded_from_designers_keys(self) -> set[str]:
        keys: set[str] = set()
        for row in self.repo.list_all():
            key = self.normalize_brand_key(row.source_brand_key)
            if not key:
                continue
            if not bool(getattr(row, "include_in_designers", True)):
                keys.add(key)
        return keys

    def resolve_vendor(self, vendor: str | None, mapping_by_key: dict[str, str] | None = None) -> tuple[str | None, str | None, str | None]:
        original = self.normalize_brand_name(vendor) or None
        if original is None:
            return None, None, None
        mapping = mapping_by_key or self.get_mapping_by_key()
        key = self.normalize_brand_key(original)
        mapped = self.normalize_brand_name(mapping.get(key) if key else None) or original
        return original, mapped, mapped

    def list_distinct_source_brands(self) -> list[str]:
        normalized_vendor = func.lower(func.trim(ParserProduct.vendor))
        rows = (
            self.db.query(
                func.min(func.trim(ParserProduct.vendor)).label("vendor"),
            )
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProduct.vendor.isnot(None))
            .filter(func.trim(ParserProduct.vendor) != "")
            .group_by(normalized_vendor)
            .order_by(normalized_vendor.asc())
            .all()
        )
        result: list[str] = []
        for (raw_vendor,) in rows:
            vendor = self.normalize_brand_name(raw_vendor)
            if vendor:
                result.append(vendor)
        return result

    def get_admin_brand_mapping_payload(self) -> dict[str, object]:
        source_brands = self.list_distinct_source_brands()
        mapping_by_key = self.get_mapping_by_key()
        raw_rows = self.repo.list_all()
        by_key = {self.normalize_brand_key(row.source_brand_key): row for row in raw_rows}

        items: list[dict[str, object]] = []
        known_targets: set[str] = set()
        for source_brand in source_brands:
            source_key = self.normalize_brand_key(source_brand)
            row = by_key.get(source_key)
            target_brand = self.normalize_brand_name((row.target_brand if row is not None else mapping_by_key.get(source_key))) or source_brand
            include_in_designers = bool(getattr(row, "include_in_designers", True)) if row is not None else True
            items.append(
                {
                    "source_brand": source_brand,
                    "target_brand": target_brand,
                    "include_in_designers": include_in_designers,
                }
            )
            known_targets.add(target_brand)

        for target in mapping_by_key.values():
            normalized_target = self.normalize_brand_name(target)
            if normalized_target:
                known_targets.add(normalized_target)

        return {
            "items": items,
            "known_targets": sorted(known_targets, key=lambda value: value.casefold()),
        }

    def save_admin_brand_mapping(self, rows: list[dict[str, object]]) -> dict[str, object]:
        prepared_by_key: dict[str, tuple[str, str, bool]] = {}
        for row in rows:
            source_brand = self.normalize_brand_name(row.get("source_brand"))
            target_brand = self.normalize_brand_name(row.get("target_brand"))
            include_in_designers = bool(row.get("include_in_designers", True))
            if not source_brand:
                continue
            if not target_brand:
                raise ValueError(f"Пустое целевое название для бренда: {source_brand}")
            source_key = self.normalize_brand_key(source_brand)
            if not source_key:
                continue
            prepared_by_key[source_key] = (source_brand, target_brand, include_in_designers)

        self.repo.delete_all()
        for source_key in sorted(prepared_by_key.keys()):
            source_brand, target_brand, include_in_designers = prepared_by_key[source_key]
            target_is_identity = self.normalize_brand_key(source_brand) == self.normalize_brand_key(target_brand)
            if target_is_identity and include_in_designers:
                continue
            self.repo.create_mapping(
                source_brand=source_brand,
                source_brand_key=source_key,
                target_brand=target_brand,
                include_in_designers=include_in_designers,
            )
        self.db.commit()
        return self.get_admin_brand_mapping_payload()
