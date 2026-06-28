from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.source_identity import normalize_base_url
from app.models import Source
from app.repositories.catalog_sources import CatalogSourceRepository
from app.services.catalog.catalog_defaults_service import CatalogDefaultsService


class SourceRegistryService:
    MANUAL_SOURCE_KEY = "manual.local"
    MANUAL_SOURCE_NAME = "Личный источник"
    MANUAL_SOURCE_URL = "manual://catalog"

    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = CatalogSourceRepository(db)

    @staticmethod
    def normalize_source_key(base_url: str) -> str:
        return normalize_base_url(base_url)

    @classmethod
    def normalize_parser_mode(cls, raw_mode: object) -> str:
        value = str(raw_mode or "").strip().lower()
        return "manual" if value == "manual" else "auto"

    @classmethod
    def derive_source_mode(cls, source: Source) -> str:
        normalized_key = str(getattr(source, "key", "") or "").strip().lower()
        if normalized_key == cls.MANUAL_SOURCE_KEY:
            return "personal"
        config = getattr(source, "parser_config", None)
        raw_mode = (config or {}).get("mode") if isinstance(config, dict) else None
        return cls.normalize_parser_mode(raw_mode)

    def ensure_manual_source(self) -> Source:
        source = self.repo.get_by_key(self.MANUAL_SOURCE_KEY)
        if source is None:
            source = self.repo.create(
                key=self.MANUAL_SOURCE_KEY,
                name=self.MANUAL_SOURCE_NAME,
                base_url=self.MANUAL_SOURCE_URL,
            )
        source.name = self.MANUAL_SOURCE_NAME
        source.base_url_normalized = normalize_base_url(self.MANUAL_SOURCE_URL)
        source.adapter_key = None
        source.parser_config = {}
        self.repo.ensure_setting(source)
        self.repo.ensure_sync_state(source)
        self.db.flush()
        return source

    def list_all(self) -> list[Source]:
        self.ensure_manual_source()
        CatalogDefaultsService(self.db).ensure()
        self.db.flush()
        return self.repo.list_all()

    def seed_from_payload(self, items: list[dict[str, Any]]) -> list[Source]:
        if self.repo.count_registry_sources() > 0:
            return self.list_all()

        seen_keys: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            base_url = str(item.get("url") or "").strip()
            if not base_url:
                continue
            base_url_normalized = normalize_base_url(base_url)
            raw_key = str(item.get("key") or "").strip().lower()
            key = raw_key or base_url_normalized
            if not key or key in seen_keys or key == self.MANUAL_SOURCE_KEY:
                continue
            seen_keys.add(key)
            source = self.repo.get_by_key(key)
            if source is None and base_url_normalized:
                source = self.repo.get_by_base_url_normalized(base_url_normalized)
            parser_config = item.get("config") if isinstance(item.get("config"), dict) else {}
            adapter_key = str(item.get("adapter_key") or "").strip() or None
            source_name = str(item.get("name") or item.get("key") or key).strip() or key
            if source is None:
                source = self.repo.create(
                    key=key,
                    name=source_name,
                    base_url=base_url,
                    adapter_key=adapter_key,
                    parser_config=dict(parser_config),
                )
            else:
                source.key = key
                source.name = source_name
                source.base_url = base_url
                source.base_url_normalized = normalize_base_url(base_url)
                source.adapter_key = adapter_key
                source.parser_config = dict(parser_config)
            setting = self.repo.ensure_setting(source)
            self.repo.ensure_sync_state(source)
            setting.is_enabled = bool(item.get("enabled", True))
            setting.is_sync_enabled = bool(item.get("sync_enabled", True))

        return self.list_all()
