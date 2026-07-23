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
    for section in payload["sections"]:
        menu = section.get("menu")
        if not menu:
            continue
        for block in menu.get("blocks") or []:
            for group in block.get("groups") or []:
                assert "title" in group
                if group.get("titleTarget") is not None:
                    assert isinstance(group["titleTarget"], dict)
                    assert "pathname" in group["titleTarget"]


def test_admin_showcase_catalog_experience_preview_available(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)

    response = client.get("/api/v1/admin/showcase/catalog-experience?view_key=default&gender=men")

    assert response.status_code == 200
    payload = response.json()
    assert payload["view"]["key"] == "default"
    assert "header" in payload["view"]
    assert isinstance(payload.get("filterGroups"), list)
    assert isinstance(payload.get("previewMetrics"), list)
    group_keys = [group["key"] for group in payload["filterGroups"]]
    assert group_keys[:2] == ["sort", "availability"]
    assert group_keys[-1] == "gender"
    assert set(group_keys).issubset({"sort", "availability", "section", "designer", "gender"})


def test_admin_showcase_catalog_products_available(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)

    response = client.get("/api/v1/admin/showcase/catalog-products?limit=12&offset=0")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("items"), list)
    assert isinstance(payload.get("total"), int)
    assert payload.get("limit") == 12
    assert payload.get("offset") == 0
    for item in payload["items"]:
        assert isinstance(item.get("id"), int)
        assert isinstance(item.get("path"), str)
        assert isinstance(item.get("name"), str)
        assert isinstance(item.get("brand"), dict)
        assert item.get("status") in {"in_stock", "preorder", "sold_out"}


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
                },
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


def test_admin_showcase_new_menu_keeps_custom_catalogs_out_of_sections() -> None:
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
                            "slug": "shirts",
                            "label": "Рубашки",
                            "is_enabled": True,
                            "children": [],
                        },
                        {
                            "id": 12,
                            "slug": "boots",
                            "label": "Ботинки",
                            "is_enabled": True,
                            "children": [],
                        },
                    ],
                }
            ],
            "categories": [
                {
                    "id": 1,
                    "slug": "new",
                    "label": "Новинки",
                    "behavior": "new",
                    "system_filter_value": None,
                    "attachments": [
                        {
                            "id": "attachment-1",
                            "kind": "custom_catalog",
                            "ref_id": 100,
                            "hidden_node_ids": [],
                        }
                    ],
                    "children": [],
                }
            ],
            "custom_catalogs": [
                {
                    "id": 100,
                    "slug": "my-choice",
                    "label": "Мой выбор",
                    "description": "",
                    "is_enabled": True,
                    "manual_products": [],
                }
            ],
            "designer_directory": [],
        }
        service._filter_product_counts_by_slug = lambda: {"shirts": 20, "boots": 30}  # type: ignore[method-assign]

        blocks = service._build_new_menu_blocks()
        collections = next(block for block in blocks if block["id"] == "new-availability")
        sections = next(block for block in blocks if block["id"] == "new-sections")

        assert [item["label"] for item in collections["items"]] == ["В наличии", "Под заказ", "Мой выбор", "Все товары"]
        assert [item["kind"] for item in collections["items"]] == ["system_link", "system_link", "curated_listing", "system_link"]
        assert [item["label"] for item in sections["items"]] == ["Ботинки", "Рубашки"]
        assert all(item["kind"] == "filter_link" for item in sections["items"])
    finally:
        db.close()


def test_admin_showcase_catalog_header_resolves_search_and_descriptions() -> None:
    db = SessionLocal()
    try:
        service = AdminShowcasePreviewService(db)
        service._taxonomy_state = {
            "filters": [],
            "categories": [],
            "custom_catalogs": [
                {
                    "id": 100,
                    "slug": "my-choice",
                    "label": "Мой выбор",
                    "description": "Описание кастомного каталога",
                    "is_enabled": True,
                    "manual_products": [],
                }
            ],
            "designer_directory": [
                {
                    "id": 7,
                    "slug": "rick-owens",
                    "label": "Rick Owens",
                    "product_count": 4,
                }
            ],
        }
        service._designer_state = {
            "designers": [
                {
                    "id": "7",
                    "name": "Rick Owens",
                    "description": "Описание дизайнера",
                }
            ],
        }

        search_payload = service.catalog_experience(view_key="default", search_params={"q": [" cargo shorts "]})
        custom_payload = service.catalog_experience(
            view_key="default",
            search_params={"ctx": ["custom"], "ctx_ref": ["my-choice"]},
        )
        designer_payload = service.catalog_experience(
            view_key="default",
            search_params={"designer": ["rick-owens"], "ctx": ["designer"], "ctx_ref": ["rick-owens"]},
        )

        assert search_payload["view"]["header"] == {
            "title": "Поиск: cargo shorts",
            "description": None,
            "source": "search",
        }
        assert custom_payload["view"]["header"] == {
            "title": "Мой выбор",
            "description": "Описание кастомного каталога",
            "source": "custom_catalog",
        }
        assert designer_payload["view"]["header"] == {
            "title": "Rick Owens",
            "description": "Описание дизайнера",
            "source": "designer",
        }
    finally:
        db.close()


def test_admin_showcase_catalog_header_resolves_mobile_multifilter_context() -> None:
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
                            "slug": "tops",
                            "label": "Верх",
                            "is_enabled": True,
                            "children": [
                                {
                                    "id": 12,
                                    "slug": "shirts",
                                    "label": "Рубашки",
                                    "is_enabled": True,
                                    "children": [],
                                },
                                {
                                    "id": 13,
                                    "slug": "hoodies",
                                    "label": "Худи",
                                    "is_enabled": True,
                                    "children": [],
                                },
                            ],
                        }
                    ],
                },
                {
                    "id": 20,
                    "slug": "womens-clothes",
                    "label": "Женская одежда",
                    "is_enabled": True,
                    "children": [
                        {
                            "id": 21,
                            "slug": "tops",
                            "label": "Верх",
                            "is_enabled": True,
                            "children": [
                                {
                                    "id": 22,
                                    "slug": "blouses",
                                    "label": "Блузы",
                                    "is_enabled": True,
                                    "children": [],
                                }
                            ],
                        }
                    ],
                },
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
                },
                {
                    "id": 2,
                    "slug": "women",
                    "label": "Женское",
                    "behavior": "gender",
                    "system_filter_value": "women",
                    "attachments": [
                        {
                            "id": "attachment-2",
                            "kind": "filter",
                            "ref_id": 20,
                            "hidden_node_ids": [],
                        }
                    ],
                    "children": [],
                }
            ],
            "custom_catalogs": [],
            "designer_directory": [],
        }

        payload = service.catalog_experience(
            view_key="default",
            search_params={
                "gender": ["men"],
                "section": ["shirts,hoodies,blouses"],
                "ctx": ["menu_filter"],
                "ctx_ref": ["mobile:tops"],
            },
        )
        new_section_payload = service.catalog_experience(
            view_key="default",
            search_params={
                "section": ["shirts"],
                "ctx": ["menu_filter"],
                "ctx_ref": ["new-section:shirts"],
            },
        )

        assert payload["view"]["header"] == {
            "title": "Верх",
            "description": None,
            "source": "menu_filter",
        }
        assert new_section_payload["view"]["header"] == {
            "title": "Рубашки",
            "description": None,
            "source": "menu_filter",
        }
    finally:
        db.close()


def test_admin_showcase_catalog_header_resolves_root_multifilter_context() -> None:
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
                            "slug": "tops",
                            "label": "Верх",
                            "is_enabled": True,
                            "children": [
                                {
                                    "id": 12,
                                    "slug": "shirts",
                                    "label": "Рубашки",
                                    "is_enabled": True,
                                    "children": [],
                                }
                            ],
                        },
                        {
                            "id": 13,
                            "slug": "outerwear",
                            "label": "Верхняя одежда",
                            "is_enabled": True,
                            "children": [],
                        },
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
                            "id": "attachment-root",
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

        payload = service.catalog_experience(
            view_key="default",
            search_params={
                "gender": ["men"],
                "section": ["shirts,outerwear"],
                "ctx": ["menu_filter"],
                "ctx_ref": ["attachment-root:10"],
            },
        )

        assert payload["view"]["header"] == {
            "title": "Одежда",
            "description": None,
            "source": "menu_filter",
        }
    finally:
        db.close()
