from __future__ import annotations

import requests

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.source_identity import normalize_base_url, normalize_host
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
    def fetch_service_sources_payload(cls) -> dict[str, dict]:
        try:
            response = requests.get(
                f"{settings.service_base_url.rstrip('/')}/api/v1/sync/sources",
                timeout=(3, 20),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            payload = []
        items = payload if isinstance(payload, list) else []
        return {
            str(item.get("key") or "").strip().lower(): item
            for item in items
            if isinstance(item, dict) and str(item.get("key") or "").strip()
        }

    @classmethod
    def derive_source_mode(cls, source_key: str, service_item: dict | None) -> str:
        normalized_key = str(source_key or "").strip().lower()
        if normalized_key == cls.MANUAL_SOURCE_KEY:
            return "personal"
        config = service_item.get("config") if isinstance(service_item, dict) else None
        raw_mode = str((config or {}).get("mode") or "auto").strip().lower()
        return "manual" if raw_mode == "manual" else "auto"

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
        source.host_normalized = normalize_host(self.MANUAL_SOURCE_URL)
        self.repo.ensure_setting(source)
        self.repo.ensure_sync_state(source)
        self.db.flush()
        return source

    def refresh_from_service(self) -> list[Source]:
        payload = list(self.fetch_service_sources_payload().values())

        seen_keys: set[str] = set()
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, dict):
                continue
            base_url = str(item.get("url") or "").strip()
            base_url_normalized = normalize_base_url(base_url)
            raw_key = str(item.get("key") or "").strip()
            key = str(raw_key or "").strip().lower() or base_url_normalized
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            source = self.repo.get_by_key(key)
            if source is None and base_url_normalized:
                source = self.repo.get_by_base_url_normalized(base_url_normalized)
            if source is None:
                source = self.repo.create(
                    key=key,
                    name=str(item.get("name") or item.get("key") or key).strip() or key,
                    base_url=base_url or f"https://{key}",
                )
            else:
                source.name = str(item.get("name") or source.name or key).strip() or key
                if base_url:
                    source.base_url = base_url
            source.base_url_normalized = normalize_base_url(source.base_url)
            source.host_normalized = normalize_host(source.base_url)
            setting = self.repo.ensure_setting(source)
            sync_state = self.repo.ensure_sync_state(source)
            if setting.is_sync_enabled is None:
                setting.is_sync_enabled = bool(item.get("sync_enabled", True))
            if setting.is_enabled is None:
                setting.is_enabled = bool(item.get("enabled", True))
            if sync_state.last_sync_status is None:
                sync_state.last_sync_status = None

        self.ensure_manual_source()
        CatalogDefaultsService(self.db).ensure()
        self.db.flush()
        return self.repo.list_all()
