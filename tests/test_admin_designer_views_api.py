from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.v1.auth as auth_module
from app.core.database import SessionLocal
from app.main import app
from app.models import AdminRole, AdminUser, DesignerSourceName, Product, ProductListing, ProductListingImage, ProductListingMember, Source, SourceSetting
from app.schemas.auth import AdminRoleCreateRequest, AdminUserCreateRequest
from app.services.auth.admin_accounts_service import AdminAccountsService
from app.services.catalog.designer_catalog_sync_service import DesignerCatalogSyncService
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
        key=f"designer-views-{marker}.example",
        name=f"Designer Views {marker}",
        base_url=f"https://designer-views-{marker}.example",
        base_url_normalized=f"designer-views-{marker}.example",
    )
    db.add(source)
    db.flush()
    db.add(SourceSetting(source_id=int(source.id), is_enabled=True, is_sync_enabled=True, show_images=True, description_mode="text"))
    db.flush()
    return source


def _ingest_product(db, *, source_id: int, marker: str, brand: str) -> Product:
    handle = f"designer-views-{marker}"
    ProductIngestService(db).apply_batch(
        source_id=int(source_id),
        items=[
            {
                "url": f"https://designer-views-{marker}.example/products/{handle}",
                "handle": handle,
                "title": "Designer Views Test",
                "description": "",
                "designer": brand,
                "category": "Outerwear",
                "tags": ["designer-views"],
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


def _create_designer_admin(db, marker: str) -> tuple[str, str, int, int]:
    accounts = AdminAccountsService(db)
    role = accounts.create_role(AdminRoleCreateRequest(
        name=f"designer-views-role-{marker}",
        permissions=["control.designers.read", "control.designers.edit"],
    ))
    login = f"designer-views-{marker}"
    accounts.create_user(AdminUserCreateRequest(login=login, password="ViewsTest12!", role_id=int(role.id)))
    return login, "ViewsTest12!", int(role.id)


def _cleanup_products(db, product_ids: list[int], listing_ids: list[int], source_id: int | None) -> None:
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


def _editor_row_and_designer(client: TestClient, brand: str) -> tuple[dict | None, dict | None]:
    response = client.get("/api/v1/admin/designers/editor")
    assert response.status_code == 200
    payload = response.json()
    row = next((item for item in payload["rows"] if item["source_brand"] == brand), None)
    designer = next((item for item in payload["designers"] if item["name"] == brand), None)
    return row, designer


def test_designer_new_marks_flow_and_independent_resets(monkeypatch) -> None:
    client = _login_client(monkeypatch, login="superadmin", password="Q7m2Lx9pRt")
    db = SessionLocal()
    marker = uuid4().hex[:12]
    brand_a = f"BrandA {marker}"
    brand_b = f"BrandB {marker}"
    source_id: int | None = None
    product_ids: list[int] = []
    listing_ids: list[int] = []
    try:
        source = _create_source(db, marker)
        source_id = int(source.id)
        product = _ingest_product(db, source_id=source_id, marker=f"{marker}-a", brand=brand_a)
        product_ids.append(int(product.id))
        listing_ids.append(int(product.primary_listing_id or 0))
        db.commit()

        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)
        db.commit()

        row, designer = _editor_row_and_designer(client, brand_a)
        assert row is not None and designer is not None
        assert row["is_new"] is True
        assert designer["is_new"] is True

        brands_reset = client.post("/api/v1/admin/designers/brands/mark-all-viewed")
        assert brands_reset.status_code == 200
        assert brands_reset.json() == {"ok": True, "marked": 1}

        row, designer = _editor_row_and_designer(client, brand_a)
        assert row is not None and designer is not None
        assert row["is_new"] is False
        assert designer["is_new"] is True

        designers_reset = client.post("/api/v1/admin/designers/designers/mark-all-viewed")
        assert designers_reset.status_code == 200
        assert designers_reset.json() == {"ok": True, "marked": 1}

        row, designer = _editor_row_and_designer(client, brand_a)
        assert row is not None and designer is not None
        assert row["is_new"] is False
        assert designer["is_new"] is False

        # A new brand arrives under the already reviewed designer: the designer
        # becomes NEW again, independent from the brand-level reset.
        product_b = _ingest_product(db, source_id=source_id, marker=f"{marker}-b", brand=brand_b)
        product_ids.append(int(product_b.id))
        listing_ids.append(int(product_b.primary_listing_id or 0))
        designer_entity = db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand_a).one()
        db.add(DesignerSourceName(source_name=brand_b, designer_name=brand_a, designer_id=int(designer_entity.designer_id)))
        db.commit()

        row_b, _ = _editor_row_and_designer(client, brand_b)
        _, designer_a = _editor_row_and_designer(client, brand_a)
        assert row_b is not None and row_b["is_new"] is True
        assert designer_a is not None and designer_a["is_new"] is True

        brands_reset_b = client.post("/api/v1/admin/designers/brands/mark-all-viewed")
        assert brands_reset_b.json() == {"ok": True, "marked": 1}

        row_b, _ = _editor_row_and_designer(client, brand_b)
        _, designer_a = _editor_row_and_designer(client, brand_a)
        assert row_b is not None and row_b["is_new"] is False
        assert designer_a is not None and designer_a["is_new"] is True

        designers_reset_b = client.post("/api/v1/admin/designers/designers/mark-all-viewed")
        # The designer re-became NEW after the BrandB arrival, so the reset re-views it.
        assert designers_reset_b.json() == {"ok": True, "marked": 1}
        _, designer_a = _editor_row_and_designer(client, brand_a)
        assert designer_a is not None and designer_a["is_new"] is False
    finally:
        _cleanup_products(db, product_ids, listing_ids, source_id)
        db.query(DesignerSourceName).filter(DesignerSourceName.source_name.in_([brand_a, brand_b])).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_designer_new_marks_are_per_admin(monkeypatch) -> None:
    superadmin_client = _login_client(monkeypatch, login="superadmin", password="Q7m2Lx9pRt")
    db = SessionLocal()
    marker = uuid4().hex[:12]
    brand = f"PerAdmin {marker}"
    source_id: int | None = None
    product_ids: list[int] = []
    listing_ids: list[int] = []
    second_login: str | None = None
    role_id: int | None = None
    try:
        source = _create_source(db, marker)
        source_id = int(source.id)
        product = _ingest_product(db, source_id=source_id, marker=f"{marker}-a", brand=brand)
        product_ids.append(int(product.id))
        listing_ids.append(int(product.primary_listing_id or 0))
        second_login, second_password, role_id = _create_designer_admin(db, f"{marker}-u")
        db.commit()

        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)
        db.commit()

        assert superadmin_client.post("/api/v1/admin/designers/brands/mark-all-viewed").json()["marked"] >= 1
        assert superadmin_client.post("/api/v1/admin/designers/designers/mark-all-viewed").json()["marked"] >= 1

        second_client = _login_client(monkeypatch, login=second_login, password=second_password)
        row, designer = _editor_row_and_designer(second_client, brand)
        assert row is not None and row["is_new"] is True
        assert designer is not None and designer["is_new"] is True
    finally:
        if second_login is not None:
            user = db.query(AdminUser).filter(AdminUser.login == second_login).one_or_none()
            if user is not None:
                db.delete(user)
        if role_id is not None:
            role = db.query(AdminRole).filter(AdminRole.id == role_id).one_or_none()
            if role is not None:
                db.delete(role)
        _cleanup_products(db, product_ids, listing_ids, source_id)
        db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).delete(synchronize_session=False)
        db.commit()
        db.close()

def test_single_brand_and_designer_view_endpoints(monkeypatch) -> None:
    client = _login_client(monkeypatch, login="superadmin", password="Q7m2Lx9pRt")
    db = SessionLocal()
    marker = uuid4().hex[:12]
    brand = f"Single {marker}"
    source_id: int | None = None
    product_ids: list[int] = []
    listing_ids: list[int] = []
    try:
        source = _create_source(db, marker)
        source_id = int(source.id)
        product = _ingest_product(db, source_id=source_id, marker=f"{marker}-a", brand=brand)
        product_ids.append(int(product.id))
        listing_ids.append(int(product.primary_listing_id or 0))
        db.commit()

        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)
        db.commit()

        row, designer = _editor_row_and_designer(client, brand)
        assert row is not None and row["is_new"] is True
        assert designer is not None and designer["is_new"] is True

        unknown_brand = client.post("/api/v1/admin/designers/brands/does-not-exist/view")
        assert unknown_brand.status_code == 404
        unknown_designer = client.post("/api/v1/admin/designers/designers/999999999/view")
        assert unknown_designer.status_code == 404

        brand_view = client.post(f"/api/v1/admin/designers/brands/{brand}/view")
        assert brand_view.status_code == 200
        row, designer = _editor_row_and_designer(client, brand)
        assert row is not None and row["is_new"] is False
        assert designer is not None and designer["is_new"] is True

        designer_id = designer["id"]
        designer_view = client.post(f"/api/v1/admin/designers/designers/{designer_id}/view")
        assert designer_view.status_code == 200
        assert designer_view.json() == {"ok": True, "designer_id": int(designer_id)}
        _, designer = _editor_row_and_designer(client, brand)
        assert designer is not None and designer["is_new"] is False
    finally:
        _cleanup_products(db, product_ids, listing_ids, source_id)
        db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).delete(synchronize_session=False)
        db.commit()
        db.close()
