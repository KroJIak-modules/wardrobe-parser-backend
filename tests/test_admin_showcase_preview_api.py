from fastapi.testclient import TestClient

import app.api.v1.auth as auth_module
from app.core.database import SessionLocal
from app.main import app
from app.services.catalog.admin_showcase_preview_service import AdminShowcasePreviewService


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


def test_admin_showcase_navigation_preview_available(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)

    response = client.get("/api/v1/admin/showcase/navigation")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("sections"), list)
    assert {section["key"] for section in payload["sections"]} == {"new", "designers", "men", "women", "sale"}


def test_admin_showcase_catalog_experience_preview_available(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)

    response = client.get("/api/v1/admin/showcase/catalog-experience?view_key=default&gender=men")

    assert response.status_code == 200
    payload = response.json()
    assert payload["view"]["key"] == "default"
    assert "header" in payload["view"]
    assert isinstance(payload.get("filterGroups"), list)
    assert isinstance(payload.get("previewMetrics"), list)
    assert [group["key"] for group in payload["filterGroups"]] == [
        "sort",
        "availability",
        "section",
        "designer",
        "gender",
    ]


def test_admin_showcase_designers_directory_preview_available(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)

    response = client.get("/api/v1/admin/showcase/designers-directory")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("alphabet"), list)
    assert isinstance(payload.get("entries"), list)


def test_admin_showcase_top_menu_designers_limited_to_two_columns() -> None:
    db = SessionLocal()
    try:
        service = AdminShowcasePreviewService(db)
        service._build_designer_options = lambda: [  # type: ignore[method-assign]
            {
                "id": str(index),
                "label": f"Designer {index}",
                "value": str(index),
                "product_count": 1000 - index,
            }
            for index in range(1, 25)
        ]

        blocks = service._build_designers_menu_blocks()

        assert len(blocks) == 2
        assert [len(block["items"]) for block in blocks] == [9, 9]
        assert [block["title"] for block in blocks] == [None, None]
        assert [item["label"] for block in blocks for item in block["items"]] == [
            f"Designer {index}" for index in range(1, 19)
        ]
    finally:
        db.close()


def test_admin_showcase_menu_uses_label_but_filter_bar_uses_display_label() -> None:
    db = SessionLocal()
    try:
        service = AdminShowcasePreviewService(db)
        service._taxonomy_state = {
            "filters": [
                {
                    "id": 10,
                    "slug": "clothes",
                    "label": "Одежда",
                    "is_enabled": True,
                    "children": [
                        {
                            "id": 11,
                            "slug": "top",
                            "label": "Верх",
                            "is_enabled": True,
                            "children": [
                                {
                                    "id": 12,
                                    "slug": "shirts",
                                    "label": "Рубашки",
                                    "display_label": "Рубашки и блузы",
                                    "is_enabled": True,
                                    "children": [],
                                }
                            ],
                        }
                    ],
                }
            ],
            "categories": [
                {
                    "id": 1,
                    "slug": "men",
                    "label": "Мужское",
                    "behavior": "gender",
                    "system_filter_value": "men",
                    "attachments": [
                        {
                            "id": "attachment-1",
                            "kind": "filter",
                            "ref_id": 10,
                            "hidden_node_ids": [],
                        }
                    ],
                    "children": [],
                }
            ],
            "custom_catalogs": [],
            "designer_directory": [],
        }

        menu_blocks = service._build_category_menu_blocks("men")
        filter_options = service._build_section_filter_options("men")

        assert menu_blocks[0]["title"] == "Одежда"
        assert menu_blocks[0]["groups"][0]["title"] == "Верх"
        assert menu_blocks[0]["groups"][0]["items"][0]["label"] == "Рубашки"
        assert filter_options[0]["label"] == "Рубашки и блузы"
    finally:
        db.close()
