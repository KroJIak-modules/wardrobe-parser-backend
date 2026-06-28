from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.models import Product, ProductDedupDecision, ProductDedupDecisionMember
from app.services.catalog.dedup_service_v2 import DedupServiceV2
from app.services.catalog.product_write_service import ProductWriteService


def _create_manual_product(db, *, title: str, designer_name: str) -> int:
    return ProductWriteService(db).create_manual_product(
        {
            "title": title,
            "description_text": "Dedup delete test",
            "designer_name": designer_name,
            "source_category_name": "Category",
            "gender": "unisex",
            "availability_mode": "in_stock",
            "visibility_status": "visible",
            "orderability_status": "orderable",
            "variants": [{"title": "Default", "price": 1000, "currency": "RUB", "available": True}],
            "manual_image_asset_ids": [],
            "manual_weight_grams": 500,
            "filter_slugs": [],
            "custom_catalog_slugs": [],
        }
    )


def _get_product(db, product_id: int):
    return db.query(Product).filter(Product.id == int(product_id)).one_or_none()


def _decision_member_sets_for_product_ids(db, product_ids: set[int]) -> list[set[int]]:
    rows = (
        db.query(ProductDedupDecision)
        .join(ProductDedupDecisionMember, ProductDedupDecisionMember.decision_id == ProductDedupDecision.id)
        .filter(ProductDedupDecision.decision_kind == "combine")
        .filter(ProductDedupDecisionMember.product_id.in_(sorted(product_ids)))
        .distinct(ProductDedupDecision.id)
        .all()
    )
    member_sets: list[set[int]] = []
    for row in rows:
        member_sets.append(
            {
                int(member.product_id)
                for member in getattr(row, "members", [])
            }
        )
    return member_sets


def test_delete_leaf_from_simple_combine_restores_remaining_product() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    try:
        designer_name = f"ZZ DEDUP DELETE SIMPLE {marker}"
        left_id = _create_manual_product(db, title=f"Leaf Left {marker}", designer_name=designer_name)
        right_id = _create_manual_product(db, title=f"Leaf Right {marker}", designer_name=designer_name)
        combined_id = DedupServiceV2(db).merge(product_ids=[left_id, right_id], merge_mode="combine")
        db.flush()

        ProductWriteService(db).delete_manual_product(product_id=left_id)
        db.flush()

        assert _get_product(db, left_id) is None
        assert _get_product(db, combined_id) is None

        right = _get_product(db, right_id)
        assert right is not None
        assert right.dedup_status == "independent"
        assert right.dedup_decision_id is None
        assert right.dedup_target_product_id is None

        assert _decision_member_sets_for_product_ids(db, {left_id, right_id}) == []
    finally:
        db.rollback()
        db.close()


def test_delete_leaf_from_nested_combine_rebuilds_chain_without_deleted_branch() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    try:
        designer_name = f"ZZ DEDUP DELETE NESTED {marker}"
        left_id = _create_manual_product(db, title=f"Leaf A {marker}", designer_name=designer_name)
        middle_id = _create_manual_product(db, title=f"Leaf B {marker}", designer_name=designer_name)
        right_id = _create_manual_product(db, title=f"Leaf C {marker}", designer_name=designer_name)

        combined_ab_id = DedupServiceV2(db).merge(product_ids=[left_id, middle_id], merge_mode="combine")
        combined_abc_id = DedupServiceV2(db).merge(product_ids=[combined_ab_id, right_id], merge_mode="combine")
        db.flush()

        ProductWriteService(db).delete_manual_product(product_id=left_id)
        db.flush()

        assert _get_product(db, left_id) is None
        assert _get_product(db, combined_ab_id) is None
        assert _get_product(db, combined_abc_id) is None

        middle = _get_product(db, middle_id)
        right = _get_product(db, right_id)
        assert middle is not None
        assert right is not None
        assert middle.dedup_status == "combined_source"
        assert right.dedup_status == "combined_source"
        assert middle.dedup_target_product_id is not None
        assert middle.dedup_target_product_id == right.dedup_target_product_id

        rebuilt_id = int(middle.dedup_target_product_id)
        rebuilt = _get_product(db, rebuilt_id)
        assert rebuilt is not None
        assert rebuilt.dedup_status == "independent"

        decision_member_sets = _decision_member_sets_for_product_ids(db, {middle_id, right_id, rebuilt_id})
        assert {middle_id, right_id} in decision_member_sets
    finally:
        db.rollback()
        db.close()


def test_delete_internal_combine_node_removes_its_subtree_and_collapses_parent_chain() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    try:
        designer_name = f"ZZ DEDUP DELETE INTERNAL {marker}"
        left_id = _create_manual_product(db, title=f"Leaf A {marker}", designer_name=designer_name)
        middle_id = _create_manual_product(db, title=f"Leaf B {marker}", designer_name=designer_name)
        right_id = _create_manual_product(db, title=f"Leaf C {marker}", designer_name=designer_name)

        combined_ab_id = DedupServiceV2(db).merge(product_ids=[left_id, middle_id], merge_mode="combine")
        combined_abc_id = DedupServiceV2(db).merge(product_ids=[combined_ab_id, right_id], merge_mode="combine")
        db.flush()

        ProductWriteService(db).delete_manual_product(product_id=combined_ab_id)
        db.flush()

        assert _get_product(db, left_id) is None
        assert _get_product(db, middle_id) is None
        assert _get_product(db, combined_ab_id) is None
        assert _get_product(db, combined_abc_id) is None

        right = _get_product(db, right_id)
        assert right is not None
        assert right.dedup_status == "independent"
        assert right.dedup_decision_id is None
        assert right.dedup_target_product_id is None

        assert _decision_member_sets_for_product_ids(db, {left_id, middle_id, right_id}) == []
    finally:
        db.rollback()
        db.close()


def test_delete_root_combine_node_removes_entire_combine_tree() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    try:
        designer_name = f"ZZ DEDUP DELETE ROOT {marker}"
        left_id = _create_manual_product(db, title=f"Leaf A {marker}", designer_name=designer_name)
        middle_id = _create_manual_product(db, title=f"Leaf B {marker}", designer_name=designer_name)
        right_id = _create_manual_product(db, title=f"Leaf C {marker}", designer_name=designer_name)

        combined_ab_id = DedupServiceV2(db).merge(product_ids=[left_id, middle_id], merge_mode="combine")
        combined_abc_id = DedupServiceV2(db).merge(product_ids=[combined_ab_id, right_id], merge_mode="combine")
        db.flush()

        ProductWriteService(db).delete_manual_product(product_id=combined_abc_id)
        db.flush()

        for product_id in (left_id, middle_id, right_id, combined_ab_id, combined_abc_id):
            assert _get_product(db, product_id) is None

        assert _decision_member_sets_for_product_ids(db, {left_id, middle_id, right_id}) == []
    finally:
        db.rollback()
        db.close()
