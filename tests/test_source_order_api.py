from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.v1.auth as auth_module
from app.core.database import SessionLocal
from app.main import app
from app.models import Source
from app.repositories.catalog_sources import CatalogSourceRepository
from app.services.catalog.source_registry_service import SourceRegistryService
from app.services.settings.settings_transfer_service import SettingsTransferService


class DummyLimiter:
    def __init__(self) -> None:
        self.failed: dict[str, int] = {}

    def is_limited(self, client_key: str) -> bool:
        return self.failed.get(client_key, 0) >= 2

    def register_failed_attempt(self, client_key: str) -> None:
        self.failed[client_key] = self.failed.get(client_key, 0) + 1


def _authorized_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(auth_module, "_login_rate_limiter", DummyLimiter())
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"login": "superadmin", "password": "Q7m2Lx9pRt"})
    assert login.status_code == 200
    return client


def _unique_key(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}.example"


def _create_source(db, *, key: str, mode: str) -> Source:
    source = Source(
        key=key,
        name=key.replace(".example", "").replace("-", " ").title(),
        base_url=f"https://{key}/",
        base_url_normalized=f"https://{key}/",
        adapter_key=f"test-{mode}",
        parser_config={"mode": mode},
    )
    db.add(source)
    db.flush()
    CatalogSourceRepository(db).ensure_setting(source)
    db.commit()
    db.refresh(source)
    return source


def test_sources_order_patch_reorders_and_persists_sort_priority(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)
    db = SessionLocal()
    auto_key = _unique_key("source-order-auto")
    manual_key = _unique_key("source-order-manual")
    original_keys = [item["key"] for item in client.get("/api/v1/sources").json()]
    try:
        created_auto = _create_source(db, key=auto_key, mode="auto")
        created_manual = _create_source(db, key=manual_key, mode="manual")

        next_keys = [manual_key, auto_key] + [key for key in original_keys if key not in {manual_key, auto_key}]
        response = client.patch("/api/v1/sources/order", json={"source_keys": next_keys})

        assert response.status_code == 200
        payload = response.json()
        assert [item["key"] for item in payload[:2]] == [manual_key, auto_key]
        assert [item["sort_priority"] for item in payload[:2]] == [1, 2]

        manual_source = db.query(Source).filter(Source.id == int(created_manual.id)).one()
        auto_source = db.query(Source).filter(Source.id == int(created_auto.id)).one()
        assert int(manual_source.setting.sort_priority) == 1
        assert int(auto_source.setting.sort_priority) == 2
    finally:
        db.query(Source).filter(Source.key.in_([auto_key, manual_key])).delete(synchronize_session=False)
        db.commit()
        SourceRegistryService(db).reorder_sources(original_keys)
        db.commit()
        db.close()


def test_sources_order_patch_rejects_incomplete_source_set(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)
    payload = client.get("/api/v1/sources").json()
    source_keys = [item["key"] for item in payload]

    response = client.patch("/api/v1/sources/order", json={"source_keys": source_keys[:-1]})

    assert response.status_code == 400
    assert "полный список" in response.text


def test_settings_transfer_preserves_source_sort_priority(monkeypatch) -> None:
    _authorized_client(monkeypatch)
    db = SessionLocal()
    service = SettingsTransferService(db)
    original_payload = service.export_payload()
    reordered_data = original_payload.model_dump()
    reordered_sources = list(reversed(reordered_data["sources"]))
    for index, item in enumerate(reordered_sources, start=1):
        item["sort_priority"] = index
    reordered_data["sources"] = reordered_sources
    try:
        result = SettingsTransferService(db).import_payload(original_payload.__class__.model_validate(reordered_data))
        assert result.ok is True

        re_exported = SettingsTransferService(db).export_payload()
        priorities_by_key = {item.key: int(item.sort_priority) for item in re_exported.sources}
        assert priorities_by_key == {
            str(item["key"]): int(item["sort_priority"])
            for item in reordered_sources
        }
    finally:
        SettingsTransferService(db).import_payload(original_payload)
        db.close()
