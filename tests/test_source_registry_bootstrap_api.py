from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.repositories.catalog_sources import CatalogSourceRepository
from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import Source
from app.services.catalog.source_registry_service import SourceRegistryService


def _internal_headers() -> dict[str, str]:
    return {"X-Internal-Token": str(settings.internal_api_token)}


def _payload(*, key: str, adapter_key: str = "demo__v1", enabled: bool = True, sync_enabled: bool = True, mode: str = "auto") -> dict:
    return {
        "sources": [
            {
                "id": 1,
                "key": key,
                "url": f"https://{key}",
                "adapter_key": adapter_key,
                "enabled": enabled,
                "sync_enabled": sync_enabled,
                "config": {"mode": mode, "strategy_sequence": ["demo"]},
            }
        ]
    }


def test_internal_bootstrap_seeds_backend_sources_when_registry_empty(monkeypatch) -> None:
    db = SessionLocal()
    client = TestClient(app)
    source_key = f"seed-{uuid4().hex[:10]}.example"
    try:
        db.query(Source).filter(Source.key == source_key).delete(synchronize_session=False)
        db.commit()
        monkeypatch.setattr(CatalogSourceRepository, "count_registry_sources", lambda self: 0)

        response = client.post(
            "/api/v1/internal/service/sources/bootstrap",
            json=_payload(key=source_key, sync_enabled=False, mode="manual"),
            headers=_internal_headers(),
        )

        assert response.status_code == 200

        created = db.query(Source).filter(Source.key == source_key).one()
        assert created.adapter_key == "demo__v1"
        assert created.parser_config["mode"] == "manual"
        assert created.setting is not None
        assert created.setting.is_enabled is True
        assert created.setting.is_sync_enabled is False
    finally:
        db.query(Source).filter(Source.key == source_key).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_internal_bootstrap_does_not_overwrite_existing_registry_sources(monkeypatch) -> None:
    db = SessionLocal()
    client = TestClient(app)
    first_key = f"seed-a-{uuid4().hex[:8]}.example"
    second_key = f"seed-b-{uuid4().hex[:8]}.example"
    try:
        created = Source(
            key=first_key,
            name=first_key,
            base_url=f"https://{first_key}",
            base_url_normalized=f"https://{first_key}",
            adapter_key="demo__v1",
            parser_config={"mode": "auto"},
        )
        db.add(created)
        db.flush()
        SourceRegistryService(db).repo.ensure_setting(created)
        db.commit()
        monkeypatch.setattr(CatalogSourceRepository, "count_registry_sources", lambda self: 1)

        response = client.post(
            "/api/v1/internal/service/sources/bootstrap",
            json=_payload(key=second_key, mode="manual"),
            headers=_internal_headers(),
        )

        assert response.status_code == 200
        assert db.query(Source).filter(Source.key == first_key).one_or_none() is not None
        assert db.query(Source).filter(Source.key == second_key).one_or_none() is None
    finally:
        db.query(Source).filter(Source.key.in_([first_key, second_key])).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_internal_sources_list_excludes_manual_source_and_returns_backend_registry() -> None:
    db = SessionLocal()
    client = TestClient(app)
    source_key = f"seed-list-{uuid4().hex[:8]}.example"
    try:
        db.query(Source).filter(Source.key == source_key).delete(synchronize_session=False)
        created = Source(
            key=source_key,
            name=source_key,
            base_url=f"https://{source_key}",
            base_url_normalized=f"https://{source_key}",
            adapter_key="demo__v1",
            parser_config={"mode": "auto"},
        )
        db.add(created)
        db.flush()
        SourceRegistryService(db).repo.ensure_setting(created)
        db.commit()

        response = client.get("/api/v1/internal/service/sources", headers=_internal_headers())

        assert response.status_code == 200
        payload = response.json()
        assert any(item["key"] == source_key for item in payload)
        assert all(item["key"] != SourceRegistryService.MANUAL_SOURCE_KEY for item in payload)
    finally:
        db.query(Source).filter(Source.key == source_key).delete(synchronize_session=False)
        db.commit()
        db.close()
