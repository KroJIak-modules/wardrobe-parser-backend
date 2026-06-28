from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException

from app.core.database import SessionLocal
from app.schemas.taxonomy import TaxonomyFilterNode, TaxonomyShowcaseCategory, TaxonomyState
from app.services.catalog.admin_editor_service import AdminEditorService
from app.services.catalog.taxonomy_service import TaxonomyService


def _clone_state(state: TaxonomyState) -> TaxonomyState:
    return TaxonomyState.model_validate(state.model_dump())


def _blank_showcase_categories(state: TaxonomyState) -> list[TaxonomyShowcaseCategory]:
    return [
        TaxonomyShowcaseCategory(code=item.code, title=item.title, attachments=[])
        for item in state.showcase_categories
    ]


def test_taxonomy_service_persists_mobile_menu_pairs_for_root_multifilters() -> None:
    db = SessionLocal()
    service = TaxonomyService(db)
    original_state = _clone_state(service.get_state())
    suffix = uuid4().hex[:8]
    try:
        payload = TaxonomyState(
            filters=[
                TaxonomyFilterNode(
                    slug=f"mobile-pair-clothes-{suffix}",
                    title=f"Mobile Pair Clothes {suffix}",
                    node_kind="multifilter",
                    mobile_pair_slug=f"mobile-pair-shoes-{suffix}",
                    children=[
                        TaxonomyFilterNode(
                            slug=f"mobile-pair-shirts-{suffix}",
                            title=f"Mobile Pair Shirts {suffix}",
                            node_kind="filter",
                        ),
                    ],
                ),
                TaxonomyFilterNode(
                    slug=f"mobile-pair-shoes-{suffix}",
                    title=f"Mobile Pair Shoes {suffix}",
                    node_kind="multifilter",
                    mobile_pair_slug=f"mobile-pair-clothes-{suffix}",
                    children=[
                        TaxonomyFilterNode(
                            slug=f"mobile-pair-boots-{suffix}",
                            title=f"Mobile Pair Boots {suffix}",
                            node_kind="filter",
                        ),
                    ],
                ),
                TaxonomyFilterNode(
                    slug=f"mobile-pair-accessories-{suffix}",
                    title=f"Mobile Pair Accessories {suffix}",
                    node_kind="multifilter",
                    children=[
                        TaxonomyFilterNode(
                            slug=f"mobile-pair-belts-{suffix}",
                            title=f"Mobile Pair Belts {suffix}",
                            node_kind="filter",
                        ),
                    ],
                ),
            ],
            custom_catalogs=[],
            showcase_categories=_blank_showcase_categories(original_state),
        )

        saved_state = service.replace_state(payload)

        roots_by_slug = {str(item.slug): item for item in saved_state.filters}
        assert roots_by_slug[f"mobile-pair-clothes-{suffix}"].mobile_pair_slug == f"mobile-pair-shoes-{suffix}"
        assert roots_by_slug[f"mobile-pair-shoes-{suffix}"].mobile_pair_slug == f"mobile-pair-clothes-{suffix}"
        assert roots_by_slug[f"mobile-pair-accessories-{suffix}"].mobile_pair_slug is None

        persisted_filters = {
            str(item.slug): item
            for item in service.repo.list_filters()
            if str(item.slug).startswith("mobile-pair-")
        }
        assert str(persisted_filters[f"mobile-pair-clothes-{suffix}"].mobile_menu_group_code or "").strip()
        assert (
            persisted_filters[f"mobile-pair-clothes-{suffix}"].mobile_menu_group_code
            == persisted_filters[f"mobile-pair-shoes-{suffix}"].mobile_menu_group_code
        )
        assert persisted_filters[f"mobile-pair-accessories-{suffix}"].mobile_menu_group_code is None
    finally:
        service.replace_state(original_state)
        db.close()


def test_taxonomy_service_rejects_non_mutual_mobile_menu_pair() -> None:
    db = SessionLocal()
    service = TaxonomyService(db)
    original_state = _clone_state(service.get_state())
    suffix = uuid4().hex[:8]
    try:
        payload = TaxonomyState(
            filters=[
                TaxonomyFilterNode(
                    slug=f"mobile-invalid-clothes-{suffix}",
                    title=f"Mobile Invalid Clothes {suffix}",
                    node_kind="multifilter",
                    mobile_pair_slug=f"mobile-invalid-shoes-{suffix}",
                    children=[
                        TaxonomyFilterNode(
                            slug=f"mobile-invalid-shirts-{suffix}",
                            title=f"Mobile Invalid Shirts {suffix}",
                            node_kind="filter",
                        ),
                    ],
                ),
                TaxonomyFilterNode(
                    slug=f"mobile-invalid-shoes-{suffix}",
                    title=f"Mobile Invalid Shoes {suffix}",
                    node_kind="multifilter",
                    children=[
                        TaxonomyFilterNode(
                            slug=f"mobile-invalid-boots-{suffix}",
                            title=f"Mobile Invalid Boots {suffix}",
                            node_kind="filter",
                        ),
                    ],
                ),
            ],
            custom_catalogs=[],
            showcase_categories=_blank_showcase_categories(original_state),
        )

        try:
            service.replace_state(payload)
        except HTTPException as exc:
            assert exc.status_code == 422
            assert "Mobile menu pair must be mutual" in str(exc.detail)
        else:
            raise AssertionError("Expected taxonomy validation error for non-mutual mobile menu pair")
    finally:
        service.replace_state(original_state)
        db.close()


def test_admin_editor_roundtrips_mobile_menu_pair_ids() -> None:
    db = SessionLocal()
    taxonomy = TaxonomyService(db)
    editor = AdminEditorService(db)
    original_state = _clone_state(taxonomy.get_state())
    suffix = uuid4().hex[:8]
    try:
        taxonomy.replace_state(
            TaxonomyState(
                filters=[
                    TaxonomyFilterNode(
                        slug=f"editor-mobile-clothes-{suffix}",
                        title=f"Editor Mobile Clothes {suffix}",
                        node_kind="multifilter",
                        children=[
                            TaxonomyFilterNode(
                                slug=f"editor-mobile-shirts-{suffix}",
                                title=f"Editor Mobile Shirts {suffix}",
                                node_kind="filter",
                            ),
                        ],
                    ),
                    TaxonomyFilterNode(
                        slug=f"editor-mobile-shoes-{suffix}",
                        title=f"Editor Mobile Shoes {suffix}",
                        node_kind="multifilter",
                        children=[
                            TaxonomyFilterNode(
                                slug=f"editor-mobile-boots-{suffix}",
                                title=f"Editor Mobile Boots {suffix}",
                                node_kind="filter",
                            ),
                        ],
                    ),
                ],
                custom_catalogs=[],
                showcase_categories=_blank_showcase_categories(original_state),
            )
        )

        editor_state = editor.list_taxonomy_editor_state()
        first_root = next(item for item in editor_state["filters"] if str(item.get("slug") or "") == f"editor-mobile-clothes-{suffix}")
        second_root = next(item for item in editor_state["filters"] if str(item.get("slug") or "") == f"editor-mobile-shoes-{suffix}")
        first_root["mobile_pair_root_id"] = int(second_root["id"])
        second_root["mobile_pair_root_id"] = int(first_root["id"])

        saved_state = editor.save_taxonomy_editor_state(editor_state)
        saved_first = next(item for item in saved_state["filters"] if str(item.get("slug") or "") == f"editor-mobile-clothes-{suffix}")
        saved_second = next(item for item in saved_state["filters"] if str(item.get("slug") or "") == f"editor-mobile-shoes-{suffix}")

        assert int(saved_first["mobile_pair_root_id"] or 0) == int(saved_second["id"])
        assert int(saved_second["mobile_pair_root_id"] or 0) == int(saved_first["id"])
    finally:
        taxonomy.replace_state(original_state)
        db.close()


def test_admin_editor_preserves_display_label_for_multifilter() -> None:
    db = SessionLocal()
    taxonomy = TaxonomyService(db)
    editor = AdminEditorService(db)
    original_state = _clone_state(taxonomy.get_state())
    suffix = uuid4().hex[:8]
    try:
        taxonomy.replace_state(
            TaxonomyState(
                filters=[
                    TaxonomyFilterNode(
                        slug=f"display-root-{suffix}",
                        title=f"Display Root {suffix}",
                        display_title="Старое отображаемое имя",
                        node_kind="multifilter",
                        children=[
                            TaxonomyFilterNode(
                                slug=f"display-leaf-{suffix}",
                                title=f"Display Leaf {suffix}",
                                node_kind="filter",
                            ),
                        ],
                    ),
                ],
                custom_catalogs=[],
                showcase_categories=_blank_showcase_categories(original_state),
            )
        )

        editor_state = editor.list_taxonomy_editor_state()
        root_node = next(item for item in editor_state["filters"] if str(item.get("slug") or "") == f"display-root-{suffix}")
        leaf_node = next(item for item in root_node["children"] if str(item.get("slug") or "") == f"display-leaf-{suffix}")
        assert str(root_node.get("display_label") or "") == "Старое отображаемое имя"
        root_node["display_label"] = "Старое отображаемое имя"
        leaf_node["display_label"] = "Можно сохранить"

        saved_state = editor.save_taxonomy_editor_state(editor_state)
        saved_root = next(item for item in saved_state["filters"] if str(item.get("slug") or "") == f"display-root-{suffix}")
        saved_leaf = next(item for item in saved_root["children"] if str(item.get("slug") or "") == f"display-leaf-{suffix}")

        assert str(saved_root.get("display_label") or "") == "Старое отображаемое имя"
        assert str(saved_leaf.get("display_label") or "") == "Можно сохранить"
    finally:
        taxonomy.replace_state(original_state)
        db.close()
