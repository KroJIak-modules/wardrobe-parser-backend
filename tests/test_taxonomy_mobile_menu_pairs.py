from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException

from app.core.database import SessionLocal
from app.models import FilterAssignmentRuntimeState
from app.schemas.taxonomy import (
    TaxonomyCustomCatalog,
    TaxonomyFilterNode,
    TaxonomyShowcaseAttachment,
    TaxonomyShowcaseCategory,
    TaxonomyState,
)
from app.services.catalog.admin_editor_service import AdminEditorService
from app.services.catalog.filter_assignment_service import ProductFilterAssignmentService
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


def test_admin_editor_roundtrips_filter_gender_restriction_flag() -> None:
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
                        slug=f"gender-flag-root-{suffix}",
                        title=f"Gender Flag Root {suffix}",
                        node_kind="multifilter",
                        children=[
                            TaxonomyFilterNode(
                                slug=f"gender-flag-leaf-{suffix}",
                                title=f"Gender Flag Leaf {suffix}",
                                node_kind="filter",
                                restrict_by_gender=False,
                            ),
                        ],
                    ),
                ],
                custom_catalogs=[],
                showcase_categories=_blank_showcase_categories(original_state),
            )
        )

        editor_state = editor.list_taxonomy_editor_state()
        root_node = next(item for item in editor_state["filters"] if str(item.get("slug") or "") == f"gender-flag-root-{suffix}")
        leaf_node = next(item for item in root_node["children"] if str(item.get("slug") or "") == f"gender-flag-leaf-{suffix}")
        assert bool(leaf_node.get("restrict_by_gender")) is False
        leaf_node["restrict_by_gender"] = True

        saved_state = editor.save_taxonomy_editor_state(editor_state)
        saved_root = next(item for item in saved_state["filters"] if str(item.get("slug") or "") == f"gender-flag-root-{suffix}")
        saved_leaf = next(item for item in saved_root["children"] if str(item.get("slug") or "") == f"gender-flag-leaf-{suffix}")

        assert bool(saved_leaf.get("restrict_by_gender")) is True
    finally:
        taxonomy.replace_state(original_state)
        db.close()


def test_admin_editor_exposes_filter_assignment_rebuild_status() -> None:
    db = SessionLocal()
    editor = AdminEditorService(db)
    previous_target = 0
    previous_applied = 0
    previous_total = 0
    previous_processed = 0
    previous_started_at = None
    previous_requested_at = None
    previous_completed_at = None
    previous_last_error = None
    try:
        state = db.query(FilterAssignmentRuntimeState).filter(FilterAssignmentRuntimeState.id == 1).one_or_none()
        if state is None:
            state = FilterAssignmentRuntimeState(id=1)
            db.add(state)
        previous_target = int(state.target_revision or 0)
        previous_applied = int(state.applied_revision or 0)
        previous_total = int(state.rebuild_total_products or 0)
        previous_processed = int(state.rebuild_processed_products or 0)
        previous_started_at = state.rebuild_started_at
        previous_requested_at = state.rebuild_requested_at
        previous_completed_at = state.rebuild_completed_at
        previous_last_error = state.last_error
        state.target_revision = 4
        state.applied_revision = 3
        state.rebuild_total_products = 200
        state.rebuild_processed_products = 80
        state.rebuild_requested_at = None
        state.rebuild_started_at = None
        state.rebuild_completed_at = None
        state.last_error = None
        db.commit()

        editor_state = editor.list_taxonomy_editor_state()
        rebuild_status = editor_state["filter_assignment_rebuild"]
        assert rebuild_status["state"] == "queued"
        assert int(rebuild_status["target_revision"]) == 4
        assert int(rebuild_status["applied_revision"]) == 3
        assert int(rebuild_status["rebuild_total_products"]) == 200
        assert int(rebuild_status["rebuild_processed_products"]) == 80
        assert int(rebuild_status["progress_percent"]) == 40
    finally:
        state = db.query(FilterAssignmentRuntimeState).filter(FilterAssignmentRuntimeState.id == 1).one_or_none()
        if state is not None:
            state.target_revision = previous_target
            state.applied_revision = previous_applied
            state.rebuild_total_products = previous_total
            state.rebuild_processed_products = previous_processed
            state.rebuild_requested_at = previous_requested_at
            state.rebuild_started_at = previous_started_at
            state.rebuild_completed_at = previous_completed_at
            state.last_error = previous_last_error
            db.commit()
        db.close()


def test_admin_editor_requests_filter_assignment_rebuild_only_once_while_pending() -> None:
    db = SessionLocal()
    editor = AdminEditorService(db)
    previous_target = 0
    previous_applied = 0
    previous_total = 0
    previous_processed = 0
    previous_started_at = None
    previous_requested_at = None
    previous_completed_at = None
    previous_last_error = None
    try:
        state = db.query(FilterAssignmentRuntimeState).filter(FilterAssignmentRuntimeState.id == 1).one_or_none()
        if state is None:
            state = FilterAssignmentRuntimeState(id=1)
            db.add(state)
            db.flush()
        previous_target = int(state.target_revision or 0)
        previous_applied = int(state.applied_revision or 0)
        previous_total = int(state.rebuild_total_products or 0)
        previous_processed = int(state.rebuild_processed_products or 0)
        previous_started_at = state.rebuild_started_at
        previous_requested_at = state.rebuild_requested_at
        previous_completed_at = state.rebuild_completed_at
        previous_last_error = state.last_error
        state.target_revision = max(previous_target, previous_applied)
        state.applied_revision = max(previous_target, previous_applied)
        state.rebuild_total_products = 0
        state.rebuild_processed_products = 0
        state.rebuild_requested_at = None
        state.rebuild_started_at = None
        state.rebuild_completed_at = None
        state.last_error = None
        db.commit()

        started_first, status_first = editor.request_filter_assignment_rebuild()
        db.commit()
        assert started_first is True
        assert status_first["state"] == "queued"
        assert int(status_first["rebuild_processed_products"]) == 0
        assert int(status_first["progress_percent"]) == 0

        started_second, status_second = editor.request_filter_assignment_rebuild()
        db.commit()
        assert started_second is False
        assert status_second["state"] == "queued"
        assert int(status_second["target_revision"]) == int(status_first["target_revision"])
    finally:
        state = db.query(FilterAssignmentRuntimeState).filter(FilterAssignmentRuntimeState.id == 1).one_or_none()
        if state is not None:
            state.target_revision = previous_target
            state.applied_revision = previous_applied
            state.rebuild_total_products = previous_total
            state.rebuild_processed_products = previous_processed
            state.rebuild_requested_at = previous_requested_at
            state.rebuild_started_at = previous_started_at
            state.rebuild_completed_at = previous_completed_at
            state.last_error = previous_last_error
            db.commit()
        db.close()


def test_taxonomy_restrict_by_gender_toggle_uses_partial_refresh_instead_of_full_rebuild(monkeypatch) -> None:
    db = SessionLocal()
    taxonomy = TaxonomyService(db)
    editor = AdminEditorService(db)
    original_state = _clone_state(taxonomy.get_state())
    suffix = uuid4().hex[:8]
    rebuild_calls: list[str] = []
    enqueue_all_calls: list[int] = []
    try:
        taxonomy.replace_state(
            TaxonomyState(
                filters=[
                    TaxonomyFilterNode(
                        slug=f"women-root-{suffix}",
                        title=f"Women Root {suffix}",
                        node_kind="multifilter",
                        children=[
                            TaxonomyFilterNode(
                                slug=f"dresses-leaf-{suffix}",
                                title=f"Dresses Leaf {suffix}",
                                node_kind="filter",
                                restrict_by_gender=True,
                                title_keywords=["dress"],
                            ),
                        ],
                    ),
                ],
                custom_catalogs=[],
                showcase_categories=[
                    TaxonomyShowcaseCategory(code="new", title="Новинки", attachments=[]),
                    TaxonomyShowcaseCategory(code="designers", title="Дизайнеры", attachments=[]),
                    TaxonomyShowcaseCategory(
                        code="women",
                        title="Женское",
                        attachments=[
                            TaxonomyShowcaseAttachment(
                                kind="filter",
                                filter_slug=f"women-root-{suffix}",
                                hidden_filter_slugs=[],
                            ),
                        ],
                    ),
                    TaxonomyShowcaseCategory(code="men", title="Мужское", attachments=[]),
                    TaxonomyShowcaseCategory(code="sale", title="Скидки", attachments=[]),
                ],
            )
        )
        state = db.query(FilterAssignmentRuntimeState).filter(FilterAssignmentRuntimeState.id == 1).one()
        state.target_revision = 1
        state.applied_revision = 1
        state.rebuild_requested_at = None
        state.rebuild_started_at = None
        state.rebuild_completed_at = None
        state.last_error = None
        db.commit()

        monkeypatch.setattr(
            ProductFilterAssignmentService,
            "request_full_rebuild",
            lambda self: rebuild_calls.append("full") or 2,
        )
        monkeypatch.setattr(
            ProductFilterAssignmentService,
            "enqueue_all_active_product_ids_after_commit",
            lambda self, batch_size=5000: enqueue_all_calls.append(int(batch_size)) or 2,
        )

        editor_state = editor.list_taxonomy_editor_state()
        root_node = next(item for item in editor_state["filters"] if str(item.get("slug") or "") == f"women-root-{suffix}")
        leaf_node = next(item for item in root_node["children"] if str(item.get("slug") or "") == f"dresses-leaf-{suffix}")
        leaf_node["restrict_by_gender"] = False

        editor.save_taxonomy_editor_state(editor_state)

        assert rebuild_calls == []
        assert enqueue_all_calls == [5000]
    finally:
        taxonomy.replace_state(original_state)
        db.close()


def test_taxonomy_display_only_change_skips_filter_assignment_update(monkeypatch) -> None:
    db = SessionLocal()
    taxonomy = TaxonomyService(db)
    editor = AdminEditorService(db)
    original_state = _clone_state(taxonomy.get_state())
    suffix = uuid4().hex[:8]
    rebuild_calls: list[str] = []
    enqueue_calls: list[list[int]] = []
    try:
        taxonomy.replace_state(
            TaxonomyState(
                filters=[
                    TaxonomyFilterNode(
                        slug=f"display-root-{suffix}",
                        title=f"Display Root {suffix}",
                        node_kind="multifilter",
                        children=[
                            TaxonomyFilterNode(
                                slug=f"display-leaf-{suffix}",
                                title=f"Display Leaf {suffix}",
                                node_kind="filter",
                                title_keywords=["display"],
                            ),
                        ],
                    ),
                ],
                custom_catalogs=[],
                showcase_categories=_blank_showcase_categories(original_state),
            )
        )
        state = db.query(FilterAssignmentRuntimeState).filter(FilterAssignmentRuntimeState.id == 1).one()
        state.target_revision = 1
        state.applied_revision = 1
        state.rebuild_requested_at = None
        state.rebuild_started_at = None
        state.rebuild_completed_at = None
        state.last_error = None
        db.commit()

        monkeypatch.setattr(
            ProductFilterAssignmentService,
            "request_full_rebuild",
            lambda self: rebuild_calls.append("full") or 2,
        )
        monkeypatch.setattr(
            ProductFilterAssignmentService,
            "enqueue_product_ids_after_commit",
            lambda self, product_ids: enqueue_calls.append(sorted(int(product_id) for product_id in product_ids)),
        )

        editor_state = editor.list_taxonomy_editor_state()
        root_node = next(item for item in editor_state["filters"] if str(item.get("slug") or "") == f"display-root-{suffix}")
        leaf_node = next(item for item in root_node["children"] if str(item.get("slug") or "") == f"display-leaf-{suffix}")
        leaf_node["display_label"] = "Показывать красиво"

        editor.save_taxonomy_editor_state(editor_state)

        assert rebuild_calls == []
        assert enqueue_calls == []
    finally:
        taxonomy.replace_state(original_state)
        db.close()


def test_taxonomy_service_strips_match_rules_from_multifilters() -> None:
    db = SessionLocal()
    taxonomy = TaxonomyService(db)
    original_state = _clone_state(taxonomy.get_state())
    suffix = uuid4().hex[:8]
    try:
        saved = taxonomy.replace_state(
            TaxonomyState(
                filters=[
                    TaxonomyFilterNode(
                        slug=f"group-rules-{suffix}",
                        title=f"Group Rules {suffix}",
                        node_kind="multifilter",
                        local_category_keywords=["must-not-stay"],
                        title_keywords=["must-not-stay"],
                        children=[
                            TaxonomyFilterNode(
                                slug=f"group-rules-leaf-{suffix}",
                                title=f"Group Rules Leaf {suffix}",
                                node_kind="filter",
                                local_category_keywords=["must-stay-local"],
                                title_keywords=["must-stay-title"],
                            ),
                        ],
                    ),
                ],
                custom_catalogs=[],
                showcase_categories=_blank_showcase_categories(original_state),
            )
        )

        root = saved.filters[0]
        leaf = root.children[0]
        assert root.node_kind == "multifilter"
        assert root.local_category_keywords == []
        assert root.title_keywords == []
        assert root.manual_product_ids == []
        assert leaf.node_kind == "filter"
        assert leaf.local_category_keywords == ["must-stay-local"]
        assert leaf.title_keywords == ["must-stay-title"]
    finally:
        taxonomy.replace_state(original_state)
        db.close()


def test_taxonomy_service_keeps_showcase_category_title_from_payload() -> None:
    db = SessionLocal()
    service = TaxonomyService(db)
    original_state = _clone_state(service.get_state())
    suffix = uuid4().hex[:8]
    custom_title = f"Кастомный заголовок {suffix}"
    try:
        payload = TaxonomyState(
            filters=[],
            custom_catalogs=[],
            showcase_categories=[
                TaxonomyShowcaseCategory(
                    code=item.code,
                    title=(custom_title if item.code == "new" else item.title),
                    attachments=[],
                )
                for item in original_state.showcase_categories
            ],
        )

        saved = service.replace_state(payload)
        updated_category = next(item for item in saved.showcase_categories if item.code == "new")
        assert updated_category.title == custom_title
    finally:
        service.replace_state(original_state)
        db.close()


def test_taxonomy_service_sanitizes_showcase_category_rules_and_order() -> None:
    db = SessionLocal()
    service = TaxonomyService(db)
    original_state = _clone_state(service.get_state())
    suffix = uuid4().hex[:8]
    filter_slug = f"showcase-filter-{suffix}"
    catalog_slug = f"showcase-catalog-{suffix}"
    filter_attachment = TaxonomyShowcaseAttachment(
        kind="filter",
        filter_slug=filter_slug,
        custom_catalog_slug=None,
        hidden_filter_slugs=[],
    )
    catalog_attachment = TaxonomyShowcaseAttachment(
        kind="custom_catalog",
        filter_slug=None,
        custom_catalog_slug=catalog_slug,
        hidden_filter_slugs=[],
    )
    try:
        saved = service.replace_state(
            TaxonomyState(
                filters=[
                    TaxonomyFilterNode(
                        slug=filter_slug,
                        title=f"Showcase Filter {suffix}",
                        node_kind="filter",
                    ),
                ],
                custom_catalogs=[
                    TaxonomyCustomCatalog(
                        slug=catalog_slug,
                        title=f"Showcase Catalog {suffix}",
                    ),
                ],
                showcase_categories=[
                    TaxonomyShowcaseCategory(code="sale", title="Скидки", attachments=[filter_attachment, catalog_attachment]),
                    TaxonomyShowcaseCategory(code="women", title="Женское", attachments=[filter_attachment]),
                    TaxonomyShowcaseCategory(code="men", title="Мужское", attachments=[filter_attachment, catalog_attachment]),
                    TaxonomyShowcaseCategory(code="designers", title="Дизайнеры", attachments=[filter_attachment]),
                    TaxonomyShowcaseCategory(code="new", title="Новинки", attachments=[filter_attachment, catalog_attachment]),
                ],
            )
        )

        categories_by_code = {item.code: item for item in saved.showcase_categories}
        assert [item.code for item in saved.showcase_categories] == ["new", "designers", "men", "women", "sale"]
        assert [item.kind for item in categories_by_code["new"].attachments] == ["custom_catalog"]
        assert [item.kind for item in categories_by_code["men"].attachments] == ["filter"]
        assert [item.kind for item in categories_by_code["women"].attachments] == ["filter"]
        assert categories_by_code["designers"].attachments == []
        assert categories_by_code["sale"].attachments == []
    finally:
        service.replace_state(original_state)
        db.close()
