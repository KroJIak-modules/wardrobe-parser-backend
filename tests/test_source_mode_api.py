from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.v1.auth as auth_module
from app.core.database import SessionLocal
from app.main import app
from app.models import ProductListing, Source, SourceSetting


class DummyLimiter:
    def is_limited(self, _client_key: str) -> bool:
        return False

    def register_failed_attempt(self, _client_key: str) -> None:
        pass


def _authorized_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(auth_module, "_login_rate_limiter", DummyLimiter())
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"login": "superadmin", "password": "Q7m2Lx9pRt"})
    assert response.status_code == 200
    return client


def test_source_mode_switch_preserves_source_and_its_listing_urls(monkeypatch) -> None:
    db = SessionLocal()
    client = _authorized_client(monkeypatch)
    source_key = f"mode-{uuid4().hex[:10]}.example"
    try:
        source = Source(
            key=source_key,
            name=source_key,
            base_url=f"https://{source_key}",
            base_url_normalized=source_key,
            adapter_key="demo__v1",
            parser_config={"mode": "auto", "strategy_sequence": ["demo"]},
        )
        db.add(source)
        db.flush()
        db.add(SourceSetting(source_id=int(source.id)))
        db.add(
            ProductListing(
                source_id=int(source.id),
                url="https://example.test/product",
                url_normalized="example.test/product",
                host_normalized="example.test",
                source_title="Product",
            )
        )
        db.commit()

        manual = client.patch(f"/api/v1/sources/{source_key}/mode", json={"mode": "manual"})
        assert manual.status_code == 200
        assert manual.json()["mode"] == "manual"

        auto = client.patch(f"/api/v1/sources/{source_key}/mode", json={"mode": "auto"})
        assert auto.status_code == 200
        assert auto.json()["mode"] == "auto"

        db.expire_all()
        stored = db.query(Source).filter(Source.key == source_key).one()
        assert stored.parser_config == {"mode": "auto", "strategy_sequence": ["demo"]}
        assert db.query(ProductListing.url).filter(ProductListing.source_id == int(source.id)).all() == [("https://example.test/product",)]
    finally:
        db.query(ProductListing).filter(ProductListing.source_id == int(source.id)).delete(synchronize_session=False)
        db.query(SourceSetting).filter(SourceSetting.source_id == int(source.id)).delete(synchronize_session=False)
        db.query(Source).filter(Source.key == source_key).delete(synchronize_session=False)
        db.commit()
        db.close()
