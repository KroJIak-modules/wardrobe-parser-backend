from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.v1.auth as auth_module
from app.core.database import SessionLocal
from app.main import app
from app.models import AdminRole, AdminUser, Product, ProductListing, ProductListingImage, ProductListingMember, Source, SourceSetting
from app.schemas.auth import AdminRoleCreateRequest, AdminUserCreateRequest
from app.services.auth.admin_accounts_service import AdminAccountsService
from app.services.catalog.product_ingest_service import ProductIngestService


class DummyLimiter:
    def __init__(self) -> None:
        self.failed: dict[str, int] = {}

    def is_limited(self, client_key: str) -> bool:
        return self.failed.get(client_key, 0) >= 2

    def register_failed_attempt(self, client_key: str) -> None:
        self.failed[client_key] = self.failed.get(client_key, 0) + 1


def _login_client(monkeypatch, *, login: str, password: str) -> TestClient:
    monkeypatch.setattr(auth_module, "_login_rate_limiter", DummyLimiter())
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"login": login, "password": password})
    assert response.status_code == 200
    return client


def _create_source(db, marker: str) -> Source:
    source = Source(
        key=f"product-views-{marker}.example",
        name=f"Product Views {marker}",
        base_url=f"https://product-views-{marker}.example",
        base_url_normalized=f"product-views-{marker}.example",
    )
    db.add(source)
    db.flush()
    db.add(SourceSetting(source_id=int(source.id), is_enabled=True, is_sync_enabled=True, show_images=True, description_mode="text"))
    db.flush()
    return source


def _ingest_product(db, *, source_id: int, marker: str) -> Product:
    handle = f"viewed-{marker}"
    ProductIngestService(db).apply_batch(
        source_id=int(source_id),
        items=[
            {
                "url": f"https://product-views-{marker}.example/products/{handle}",
                "handle": handle,
                "title": "Views Test Product",
                "description": "",
                "designer": "Views Test Designer",
                "category": "Outerwear",
                "tags": ["product-views"],
                "gender": "unisex",
                "source_weight_grams": 500,
                "orderability_status": "orderable",
                "status_reason": None,
                "variants": [{"title": "UNI", "price": 120.0, "currency": "RUB", "available": True}],
                "images": [],
            }
        ],
    )
    db.flush()
    listing = db.query(ProductListing).filter(ProductListing.source_id == int(source_id), ProductListing.handle == handle).one()
    db.add(ProductListingImage(listing_id=int(listing.id), position=0, url=f"https://images.example/{handle}.jpg"))
    return db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()


def _create_readonly_admin(db, marker: str) -> tuple[str, str, int]:
    accounts = AdminAccountsService(db)
    role = accounts.create_role(AdminRoleCreateRequest(
        name=f"product-views-role-{marker}",
        permissions=["control.products.read"],
    ))
    login = f"product-views-{marker}"
    accounts.create_user(AdminUserCreateRequest(
        login=login,
        password="ViewsTest12!",
        role_id=int(role.id),
    ))
    return login, "ViewsTest12!", int(role.id)


def _table_item_by_product_id(client: TestClient, product_id: int) -> dict:
    response = client.get(f"/api/v1/admin/products/table?limit=500&offset=0")
    assert response.status_code == 200
    items = [item for item in response.json()["items"] if item["id"] == product_id]
    assert items, f"product {product_id} missing from admin table"
    return items[0]


def test_admin_table_marks_new_product_and_view_endpoint_clears_flag(monkeypatch) -> None:
    client = _login_client(monkeypatch, login="superadmin", password="Q7m2Lx9pRt")
    db = SessionLocal()
    marker = uuid4().hex[:12]
    source_id: int | None = None
    product_id: int | None = None
    listing_id: int | None = None
    try:
        source = _create_source(db, marker)
        source_id = int(source.id)
        product = _ingest_product(db, source_id=source_id, marker=marker)
        product_id = int(product.id)
        listing_id = int(product.primary_listing_id or 0)
        db.commit()

        item = _table_item_by_product_id(client, product_id)
        assert item["is_new"] is True

        viewed = client.post(f"/api/v1/admin/products/{product_id}/view")
        assert viewed.status_code == 200
        assert viewed.json() == {"ok": True, "product_id": product_id}

        item = _table_item_by_product_id(client, product_id)
        assert item["is_new"] is False

        repeat = client.post(f"/api/v1/admin/products/{product_id}/view")
        assert repeat.status_code == 200
        item = _table_item_by_product_id(client, product_id)
        assert item["is_new"] is False
    finally:
        if product_id is not None:
            db.query(ProductListingMember).filter(ProductListingMember.product_id == product_id).delete(synchronize_session=False)
            db.query(Product).filter(Product.id == product_id).delete(synchronize_session=False)
        if listing_id is not None:
            db.query(ProductListing).filter(ProductListing.id == listing_id).delete(synchronize_session=False)
        if source_id is not None:
            db.query(SourceSetting).filter(SourceSetting.source_id == source_id).delete(synchronize_session=False)
            db.query(Source).filter(Source.id == source_id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_view_endpoint_unknown_product_returns_404(monkeypatch) -> None:
    client = _login_client(monkeypatch, login="superadmin", password="Q7m2Lx9pRt")
    response = client.post("/api/v1/admin/products/999999999/view")
    assert response.status_code == 404


def test_new_only_filter_shows_only_unviewed_products(monkeypatch) -> None:
    client = _login_client(monkeypatch, login="superadmin", password="Q7m2Lx9pRt")
    db = SessionLocal()
    marker = uuid4().hex[:12]
    source_id: int | None = None
    viewed_product_id: int | None = None
    new_product_id: int | None = None
    listing_ids: list[int] = []
    try:
        source = _create_source(db, marker)
        source_id = int(source.id)
        viewed_product = _ingest_product(db, source_id=source_id, marker=f"{marker}-a")
        viewed_product_id = int(viewed_product.id)
        listing_ids.append(int(viewed_product.primary_listing_id or 0))
        new_product = _ingest_product(db, source_id=source_id, marker=f"{marker}-b")
        new_product_id = int(new_product.id)
        listing_ids.append(int(new_product.primary_listing_id or 0))
        db.commit()

        assert client.post(f"/api/v1/admin/products/{viewed_product_id}/view").status_code == 200

        all_response = client.get("/api/v1/admin/products/table?limit=500&offset=0")
        assert all_response.status_code == 200
        all_ids = {item["id"] for item in all_response.json()["items"]}
        assert {viewed_product_id, new_product_id} <= all_ids

        new_only_response = client.get("/api/v1/admin/products/table?limit=500&offset=0&new_only=1")
        assert new_only_response.status_code == 200
        new_only_payload = new_only_response.json()
        new_only_ids = {item["id"] for item in new_only_payload["items"]}
        assert viewed_product_id not in new_only_ids
        assert new_product_id in new_only_ids
        assert all(item["is_new"] for item in new_only_payload["items"])
        assert new_only_payload["total"] >= 1

        facets_response = client.get("/api/v1/admin/products/table/facets?new_only=1")
        assert facets_response.status_code == 200
        facets_payload = facets_response.json()
        assert facets_payload["total"] == new_only_payload["total"]

        viewed_still_listed_without_filter = client.get("/api/v1/admin/products/table?limit=500&offset=0")
        item_by_id = {item["id"]: item for item in viewed_still_listed_without_filter.json()["items"]}
        assert item_by_id[viewed_product_id]["is_new"] is False
        assert item_by_id[new_product_id]["is_new"] is True
    finally:
        for product_id in (viewed_product_id, new_product_id):
            if product_id is not None:
                db.query(ProductListingMember).filter(ProductListingMember.product_id == product_id).delete(synchronize_session=False)
                db.query(Product).filter(Product.id == product_id).delete(synchronize_session=False)
        for listing_id in listing_ids:
            if listing_id:
                db.query(ProductListing).filter(ProductListing.id == listing_id).delete(synchronize_session=False)
        if source_id is not None:
            db.query(SourceSetting).filter(SourceSetting.source_id == source_id).delete(synchronize_session=False)
            db.query(Source).filter(Source.id == source_id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_viewed_state_is_per_admin(monkeypatch) -> None:
    superadmin_client = _login_client(monkeypatch, login="superadmin", password="Q7m2Lx9pRt")
    db = SessionLocal()
    marker = uuid4().hex[:12]
    source_id: int | None = None
    product_id: int | None = None
    listing_id: int | None = None
    second_login: str | None = None
    role_id: int | None = None
    try:
        source = _create_source(db, marker)
        source_id = int(source.id)
        product = _ingest_product(db, source_id=source_id, marker=marker)
        product_id = int(product.id)
        listing_id = int(product.primary_listing_id or 0)
        second_login, second_password, role_id = _create_readonly_admin(db, marker)
        db.commit()

        assert _table_item_by_product_id(superadmin_client, product_id)["is_new"] is True
        assert superadmin_client.post(f"/api/v1/admin/products/{product_id}/view").status_code == 200
        assert _table_item_by_product_id(superadmin_client, product_id)["is_new"] is False

        second_client = _login_client(monkeypatch, login=second_login, password=second_password)
        assert _table_item_by_product_id(second_client, product_id)["is_new"] is True
        assert second_client.post(f"/api/v1/admin/products/{product_id}/view").status_code == 200
        assert _table_item_by_product_id(second_client, product_id)["is_new"] is False
    finally:
        if second_login is not None:
            user = db.query(AdminUser).filter(AdminUser.login == second_login).one_or_none()
            if user is not None:
                db.delete(user)
        if role_id is not None:
            role = db.query(AdminRole).filter(AdminRole.id == role_id).one_or_none()
            if role is not None:
                db.delete(role)
        if product_id is not None:
            db.query(ProductListingMember).filter(ProductListingMember.product_id == product_id).delete(synchronize_session=False)
            db.query(Product).filter(Product.id == product_id).delete(synchronize_session=False)
        if listing_id is not None:
            db.query(ProductListing).filter(ProductListing.id == listing_id).delete(synchronize_session=False)
        if source_id is not None:
            db.query(SourceSetting).filter(SourceSetting.source_id == source_id).delete(synchronize_session=False)
            db.query(Source).filter(Source.id == source_id).delete(synchronize_session=False)
        db.commit()
        db.close()

def test_mark_all_viewed_endpoint_respects_filters_and_is_idempotent(monkeypatch) -> None:
    client = _login_client(monkeypatch, login="superadmin", password="Q7m2Lx9pRt")
    db = SessionLocal()
    marker = uuid4().hex[:12]
    source_id: int | None = None
    product_ids: list[int] = []
    listing_ids: list[int] = []
    try:
        source = _create_source(db, marker)
        source_id = int(source.id)
        for index in range(2):
            product = _ingest_product(db, source_id=source_id, marker=f"{marker}-{index}")
            product_ids.append(int(product.id))
            listing_ids.append(int(product.primary_listing_id or 0))
        db.commit()

        first_mark = client.post("/api/v1/admin/products/mark-all-viewed")
        assert first_mark.status_code == 200
        assert first_mark.json() == {"ok": True, "marked": 2}

        repeat_mark = client.post("/api/v1/admin/products/mark-all-viewed")
        assert repeat_mark.status_code == 200
        assert repeat_mark.json() == {"ok": True, "marked": 0}

        table = client.get("/api/v1/admin/products/table?limit=500&offset=0")
        items = {item["id"]: item for item in table.json()["items"]}
        assert all(items[product_id]["is_new"] is False for product_id in product_ids)
    finally:
        for product_id in product_ids:
            if product_id is not None:
                db.query(ProductListingMember).filter(ProductListingMember.product_id == product_id).delete(synchronize_session=False)
                db.query(Product).filter(Product.id == product_id).delete(synchronize_session=False)
        for listing_id in listing_ids:
            if listing_id:
                db.query(ProductListing).filter(ProductListing.id == listing_id).delete(synchronize_session=False)
        if source_id is not None:
            db.query(SourceSetting).filter(SourceSetting.source_id == source_id).delete(synchronize_session=False)
            db.query(Source).filter(Source.id == source_id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_mark_all_viewed_endpoint_new_only_marks_only_unseen(monkeypatch) -> None:
    client = _login_client(monkeypatch, login="superadmin", password="Q7m2Lx9pRt")
    db = SessionLocal()
    marker = uuid4().hex[:12]
    source_id: int | None = None
    viewed_product_id: int | None = None
    new_product_id: int | None = None
    listing_ids: list[int] = []
    try:
        source = _create_source(db, marker)
        source_id = int(source.id)
        viewed_product = _ingest_product(db, source_id=source_id, marker=f"{marker}-a")
        viewed_product_id = int(viewed_product.id)
        listing_ids.append(int(viewed_product.primary_listing_id or 0))
        new_product = _ingest_product(db, source_id=source_id, marker=f"{marker}-b")
        new_product_id = int(new_product.id)
        listing_ids.append(int(new_product.primary_listing_id or 0))
        db.commit()

        assert client.post(f"/api/v1/admin/products/{viewed_product_id}/view").status_code == 200

        mark_new = client.post("/api/v1/admin/products/mark-all-viewed?new_only=1")
        assert mark_new.status_code == 200
        assert mark_new.json() == {"ok": True, "marked": 1}

        new_only_table = client.get("/api/v1/admin/products/table?limit=500&offset=0&new_only=1")
        assert new_only_table.json()["items"] == []
        assert new_only_table.json()["total"] == 0

        full_table = client.get("/api/v1/admin/products/table?limit=500&offset=0")
        items = {item["id"]: item for item in full_table.json()["items"]}
        assert items[viewed_product_id]["is_new"] is False
        assert items[new_product_id]["is_new"] is False
    finally:
        for product_id in (viewed_product_id, new_product_id):
            if product_id is not None:
                db.query(ProductListingMember).filter(ProductListingMember.product_id == product_id).delete(synchronize_session=False)
                db.query(Product).filter(Product.id == product_id).delete(synchronize_session=False)
        for listing_id in listing_ids:
            if listing_id:
                db.query(ProductListing).filter(ProductListing.id == listing_id).delete(synchronize_session=False)
        if source_id is not None:
            db.query(SourceSetting).filter(SourceSetting.source_id == source_id).delete(synchronize_session=False)
            db.query(Source).filter(Source.id == source_id).delete(synchronize_session=False)
        db.commit()
        db.close()
