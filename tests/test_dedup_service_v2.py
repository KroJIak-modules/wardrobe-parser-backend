from __future__ import annotations

from types import SimpleNamespace

from app.services.catalog.dedup_service_v2 import DedupServiceV2


def _product(product_id: int, *, primary_listing_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=int(product_id),
        lifecycle_status="active",
        dedup_status="independent",
        dedup_decision_id=None,
        dedup_target_product_id=None,
        primary_listing_id=int(primary_listing_id),
        designer_id=None,
        gender="unisex",
        availability_mode="by_order",
        manual_weight_grams=None,
        weight_rule_id=None,
        visibility_status="visible",
        presentation=None,
    )


def _listing(listing_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=int(listing_id), images=[])


class _FakeProductsRepo:
    def __init__(self) -> None:
        self.products = {
            1: _product(1, primary_listing_id=101),
            2: _product(2, primary_listing_id=102),
            3: _product(3, primary_listing_id=103),
        }
        self.listings_by_product_id = {
            1: [_listing(101)],
            2: [_listing(102)],
            3: [_listing(103)],
        }
        self.created_products: list[int] = []
        self.included_memberships: list[tuple[int, int]] = []
        self.owner_memberships: list[tuple[int, int]] = []
        self.deleted_product_ids: list[int] = []

    def get_product(self, product_id: int):
        return self.products.get(int(product_id))

    def create_product(self, **kwargs):
        next_id = max(self.products) + 1
        created = SimpleNamespace(
            id=next_id,
            lifecycle_status=str(kwargs.get("lifecycle_status") or "active"),
            dedup_status=str(kwargs.get("dedup_status") or "independent"),
            dedup_decision_id=kwargs.get("dedup_decision_id"),
            dedup_target_product_id=kwargs.get("dedup_target_product_id"),
            primary_listing_id=None,
            designer_id=kwargs.get("designer_id"),
            gender=kwargs.get("gender"),
            availability_mode=kwargs.get("availability_mode"),
            manual_weight_grams=kwargs.get("manual_weight_grams"),
            weight_rule_id=kwargs.get("weight_rule_id"),
            visibility_status=kwargs.get("visibility_status"),
            presentation=None,
        )
        self.products[next_id] = created
        self.listings_by_product_id[next_id] = []
        self.created_products.append(next_id)
        return created

    def list_product_listings(self, product_id: int):
        return list(self.listings_by_product_id.get(int(product_id), []))

    def ensure_included_membership(self, *, product_id: int, listing_id: int):
        listing = _listing(listing_id)
        current = self.listings_by_product_id.setdefault(int(product_id), [])
        if not any(int(item.id) == int(listing_id) for item in current):
            current.append(listing)
        self.included_memberships.append((int(product_id), int(listing_id)))
        return SimpleNamespace(product_id=int(product_id), listing_id=int(listing_id), membership_kind="included")

    def ensure_owner_membership(self, *, product_id: int, listing_id: int):
        listing = _listing(listing_id)
        self.listings_by_product_id[int(product_id)] = [item for item in self.listings_by_product_id.get(int(product_id), []) if int(item.id) != int(listing_id)] + [listing]
        for owner_product_id, items in list(self.listings_by_product_id.items()):
            if int(owner_product_id) == int(product_id):
                continue
            self.listings_by_product_id[int(owner_product_id)] = [item for item in items if int(item.id) != int(listing_id)]
        self.owner_memberships.append((int(product_id), int(listing_id)))
        return SimpleNamespace(product_id=int(product_id), listing_id=int(listing_id), membership_kind="owner")

    def list_gallery_scope(self, *, product_id: int, listing_id: int):
        return []

    def replace_gallery_scope_with_source_images(self, *, product_id: int, listing_id: int, listing_images):
        return None

    def set_product_primary_listing(self, *, product_id: int, primary_listing_id: int | None) -> None:
        self.products[int(product_id)].primary_listing_id = primary_listing_id

    def set_product_dedup_state(
        self,
        *,
        product_id: int,
        dedup_status: str,
        dedup_decision_id: int | None,
        dedup_target_product_id: int | None,
    ) -> None:
        product = self.products[int(product_id)]
        product.dedup_status = str(dedup_status)
        product.dedup_decision_id = dedup_decision_id
        product.dedup_target_product_id = dedup_target_product_id

    def set_product_lifecycle_status(self, *, product_id: int, lifecycle_status: str) -> None:
        self.products[int(product_id)].lifecycle_status = str(lifecycle_status)

    def delete_product_hard(self, product_id: int) -> None:
        self.deleted_product_ids.append(int(product_id))
        self.products.pop(int(product_id), None)
        self.listings_by_product_id.pop(int(product_id), None)


class _FakeDecisionsRepo:
    def __init__(self) -> None:
        self.created: list[SimpleNamespace] = []
        self.members_by_decision_id: dict[int, list[int]] = {}
        self.deleted_candidates_for: list[list[int]] = []
        self.deleted_decision_ids: list[int] = []
        self.restored_candidates: list[dict] = []
        self.decision_by_id: dict[int, SimpleNamespace] = {}
        self.blocked_created_product_ids: set[int] = set()

    def get_candidate_by_pair(self, *, product_ids):
        normalized = sorted(int(product_id) for product_id in product_ids)
        if normalized == [1, 2]:
            return SimpleNamespace(left_product_id=1, right_product_id=2, score=0.98, reasons=["same_title"])
        return None

    def create_decision(self, *, decision_kind: str, created_product_id: int | None):
        decision_id = len(self.created) + 1
        entity = SimpleNamespace(
            id=decision_id,
            decision_kind=str(decision_kind),
            created_product_id=created_product_id,
            undo_payload=None,
            members=[],
        )
        self.created.append(entity)
        self.decision_by_id[decision_id] = entity
        return entity

    def add_members(self, *, decision_id: int, product_ids: list[int]) -> None:
        normalized = sorted({int(product_id) for product_id in product_ids})
        self.members_by_decision_id[int(decision_id)] = normalized
        decision = self.decision_by_id[int(decision_id)]
        decision.members = [SimpleNamespace(product_id=product_id) for product_id in normalized]

    def delete_candidates_for_product_ids(self, *, product_ids) -> None:
        self.deleted_candidates_for.append(sorted(int(product_id) for product_id in product_ids))

    def delete_candidate_by_pair(self, *, product_ids) -> None:
        return None

    def get_decision(self, decision_id: int):
        return self.decision_by_id.get(int(decision_id))

    def has_dependent_decisions(self, *, created_product_id: int, exclude_decision_id: int) -> bool:
        return int(created_product_id) in self.blocked_created_product_ids

    def delete_decision(self, decision) -> None:
        self.deleted_decision_ids.append(int(decision.id))
        self.decision_by_id.pop(int(decision.id), None)

    def restore_candidate(self, *, left_product_id: int, right_product_id: int, score: float, reasons: list[str]) -> None:
        self.restored_candidates.append(
            {
                "left_product_id": int(left_product_id),
                "right_product_id": int(right_product_id),
                "score": float(score),
                "reasons": list(reasons),
            }
        )

    def list_candidates(self, *, limit: int, offset: int):
        return []

    def count_candidates(self) -> int:
        return 0

    def list_decisions(self, *, limit: int, offset: int):
        return list(self.created)[offset : offset + limit]


class _FakeFilterAssignments:
    def __init__(self) -> None:
        self.enqueued: list[list[int]] = []

    def enqueue_product_ids_after_commit(self, product_ids) -> None:
        self.enqueued.append([int(product_id) for product_id in product_ids])


def _service() -> tuple[DedupServiceV2, _FakeProductsRepo, _FakeDecisionsRepo, _FakeFilterAssignments]:
    service = DedupServiceV2(db=SimpleNamespace(add=lambda *_: None, flush=lambda: None, delete=lambda *_: None, info={}))  # type: ignore[arg-type]
    products = _FakeProductsRepo()
    decisions = _FakeDecisionsRepo()
    filter_assignments = _FakeFilterAssignments()
    service.products = products
    service.decisions = decisions
    service.filter_assignments = filter_assignments
    return service, products, decisions, filter_assignments


def test_merge_combine_creates_new_product_and_hides_originals() -> None:
    service, products, decisions, filter_assignments = _service()

    created_product_id = service.merge(product_ids=[1, 2], merge_mode="combine")

    assert created_product_id == 4
    assert decisions.created[0].decision_kind == "combine"
    assert decisions.created[0].created_product_id == 4
    assert products.products[1].dedup_status == "combined_source"
    assert products.products[2].dedup_status == "combined_source"
    assert products.products[1].dedup_target_product_id == 4
    assert products.products[2].dedup_target_product_id == 4
    assert sorted(products.listings_by_product_id[4], key=lambda item: item.id)[0].id == 101
    assert sorted(products.listings_by_product_id[4], key=lambda item: item.id)[1].id == 102
    assert filter_assignments.enqueued[-1] == [1, 2, 4]


def test_merge_keep_left_hides_only_right_product() -> None:
    service, products, decisions, filter_assignments = _service()

    returned_product_id = service.merge(product_ids=[1, 2], merge_mode="keep_left")

    assert returned_product_id == 1
    assert decisions.created[0].decision_kind == "keep_left"
    assert decisions.created[0].created_product_id is None
    assert products.products[1].dedup_status == "independent"
    assert products.products[2].dedup_status == "hidden_by_keep"
    assert products.products[2].dedup_target_product_id == 1
    assert filter_assignments.enqueued[-1] == [1, 2]
    assert products.created_products == []


def test_undo_combine_restores_products_and_deletes_created_product() -> None:
    service, products, decisions, filter_assignments = _service()
    created_product_id = service.merge(product_ids=[1, 2], merge_mode="combine")

    service.undo(decision_id=1)

    assert created_product_id == 4
    assert products.products[1].dedup_status == "independent"
    assert products.products[2].dedup_status == "independent"
    assert products.products[1].dedup_target_product_id is None
    assert products.products[2].dedup_target_product_id is None
    assert products.deleted_product_ids == [4]
    assert decisions.deleted_decision_ids == [1]
    assert decisions.restored_candidates == [
        {
            "left_product_id": 1,
            "right_product_id": 2,
            "score": 0.98,
            "reasons": ["same_title"],
        }
    ]
    assert filter_assignments.enqueued[-1] == [1, 2, 4]


def test_undo_keep_right_restores_hidden_product() -> None:
    service, products, decisions, filter_assignments = _service()
    returned_product_id = service.merge(product_ids=[1, 2], merge_mode="keep_right")

    assert returned_product_id == 2

    service.undo(decision_id=1)

    assert products.products[1].dedup_status == "independent"
    assert products.products[2].dedup_status == "independent"
    assert products.deleted_product_ids == []
    assert decisions.deleted_decision_ids == [1]
    assert filter_assignments.enqueued[-1] == [1, 2]


def test_can_undo_combine_is_blocked_by_dependent_decision() -> None:
    service, products, decisions, _filter_assignments = _service()
    service.merge(product_ids=[1, 2], merge_mode="combine")
    decisions.blocked_created_product_ids.add(4)

    can_undo, reason = service.can_undo_decision(decisions.decision_by_id[1])

    assert can_undo is False
    assert reason == "Есть более новые решения, зависящие от этого объединения"


def test_describe_decision_action_returns_decision_kind() -> None:
    service, _products, _decisions, _filter_assignments = _service()

    assert service.describe_decision_action(SimpleNamespace(decision_kind="combine", undo_payload=None)) == "combine"
    assert service.describe_decision_action(SimpleNamespace(decision_kind="keep_right", undo_payload=None)) == "keep_right"


def test_undo_legacy_combine_restores_owner_memberships_before_primary_listing() -> None:
    service, products, decisions, filter_assignments = _service()
    legacy_created = _product(4, primary_listing_id=101)
    products.products[4] = legacy_created
    products.listings_by_product_id[4] = [_listing(101), _listing(102)]
    products.listings_by_product_id[1] = []
    products.listings_by_product_id[2] = []

    decisions.decision_by_id[91] = SimpleNamespace(
        id=91,
        decision_kind="combine",
        created_product_id=4,
        undo_payload={
            "version": 1,
            "products": [
                {
                    "product_id": 1,
                    "lifecycle_status": "active",
                    "primary_listing_id": 101,
                    "dedup_status": "independent",
                    "dedup_decision_id": None,
                    "dedup_target_product_id": None,
                },
                {
                    "product_id": 2,
                    "lifecycle_status": "active",
                    "primary_listing_id": 102,
                    "dedup_status": "independent",
                    "dedup_decision_id": None,
                    "dedup_target_product_id": None,
                },
            ],
            "listing_owners": [
                {"listing_id": 101, "product_id": 1},
                {"listing_id": 102, "product_id": 2},
            ],
            "gallery_scopes": [],
        },
        members=[],
    )

    service.undo(decision_id=91)

    assert products.owner_memberships == [(1, 101), (2, 102)]
    assert products.products[1].primary_listing_id == 101
    assert products.products[2].primary_listing_id == 102
    assert products.deleted_product_ids == [4]
    assert filter_assignments.enqueued[-1] == [1, 2, 4]
