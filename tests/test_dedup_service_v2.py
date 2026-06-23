from types import SimpleNamespace

from app.services.catalog.dedup_service_v2 import DedupServiceV2


class _FakeProductsRepo:
    def __init__(self, products):
        self._products = products

    def list_products_for_dedup_by_ids(self, product_ids):
        wanted = {int(product_id) for product_id in product_ids}
        return [product for product in self._products if int(product.id) in wanted]


class _FakeDecisionsRepo:
    def __init__(self, candidates=None):
        self._candidates = list(candidates or [])

    def list_candidates(self, *, limit: int, offset: int):
        return list(self._candidates)[offset : offset + limit]

    def count_candidates(self) -> int:
        return len(self._candidates)

    def list_decisions(self, *, limit: int, offset: int):
        return []


class _UndoFakeProductsRepo:
    def __init__(self, *, created_product_id: int = 20, current_listing_ids: list[int] | None = None):
        self.created_product_id = int(created_product_id)
        self.products = {
            self.created_product_id: SimpleNamespace(id=self.created_product_id),
        }
        self.current_listing_ids = list(current_listing_ids or [101, 102])

    def get_product(self, product_id: int):
        return self.products.get(int(product_id))

    def list_product_listings(self, product_id: int):
        if int(product_id) == self.created_product_id:
            return [SimpleNamespace(id=listing_id) for listing_id in self.current_listing_ids]
        return []

    def delete_product_hard(self, product_id: int) -> None:
        self.products.pop(int(product_id), None)

    def set_product_primary_listing(self, *, product_id: int, primary_listing_id: int | None) -> None:
        product = self.products.get(int(product_id))
        if product is not None:
            product.primary_listing_id = primary_listing_id

    def set_product_lifecycle_status(self, *, product_id: int, lifecycle_status: str) -> None:
        product = self.products.get(int(product_id))
        if product is not None:
            product.lifecycle_status = lifecycle_status


class _ChainUndoFakeProductsRepo:
    def __init__(self) -> None:
        self.products = {
            1: SimpleNamespace(id=1, lifecycle_status="merged", primary_listing_id=101),
            2: SimpleNamespace(id=2, lifecycle_status="merged", primary_listing_id=102),
            3: SimpleNamespace(id=3, lifecycle_status="merged", primary_listing_id=103),
            20: SimpleNamespace(id=20, lifecycle_status="merged", primary_listing_id=101),
            30: SimpleNamespace(id=30, lifecycle_status="active", primary_listing_id=101),
        }
        self.membership_by_listing_id = {
            101: 30,
            102: 30,
            103: 30,
        }

    def get_product(self, product_id: int):
        return self.products.get(int(product_id))

    def list_product_listings(self, product_id: int):
        product_id = int(product_id)
        listing_ids = sorted(
            listing_id
            for listing_id, owner_product_id in self.membership_by_listing_id.items()
            if int(owner_product_id) == product_id
        )
        return [SimpleNamespace(id=listing_id) for listing_id in listing_ids]

    def ensure_membership(self, *, product_id: int, listing_id: int):
        self.membership_by_listing_id[int(listing_id)] = int(product_id)
        return SimpleNamespace(product_id=int(product_id), listing_id=int(listing_id))

    def list_gallery_scope(self, *, product_id: int, listing_id: int):
        return []

    def delete_product_hard(self, product_id: int) -> None:
        product_id = int(product_id)
        self.products.pop(product_id, None)
        orphan_listing_ids = [
            listing_id
            for listing_id, owner_product_id in self.membership_by_listing_id.items()
            if int(owner_product_id) == product_id
        ]
        for listing_id in orphan_listing_ids:
            self.membership_by_listing_id.pop(listing_id, None)

    def set_product_primary_listing(self, *, product_id: int, primary_listing_id: int | None) -> None:
        product = self.products.get(int(product_id))
        if product is not None:
            product.primary_listing_id = primary_listing_id

    def set_product_lifecycle_status(self, *, product_id: int, lifecycle_status: str) -> None:
        product = self.products.get(int(product_id))
        if product is not None:
            product.lifecycle_status = lifecycle_status


class _ChainUndoFakeDb:
    def flush(self) -> None:
        return None

    def add(self, _entity) -> None:
        return None

    def delete(self, _entity) -> None:
        return None


class _UndoFakeDecisionsRepo:
    def __init__(self, decision, *, has_dependents: bool = False):
        self._decision = decision
        self._has_dependents = has_dependents
        self.deleted_ids: list[int] = []

    def list_decisions(self, *, limit: int, offset: int):
        return []

    def get_decision(self, decision_id: int):
        if int(decision_id) == int(self._decision.id):
            return self._decision
        return None

    def has_dependent_decisions(self, *, created_product_id: int, exclude_decision_id: int) -> bool:
        assert int(created_product_id) == int(self._decision.created_product_id or 0)
        assert int(exclude_decision_id) == int(self._decision.id)
        return self._has_dependents

    def delete_decision(self, decision) -> None:
        self.deleted_ids.append(int(decision.id))


def test_list_candidates_prioritizes_newer_pairs() -> None:
    def _product(product_id: int):
        listing = SimpleNamespace(
            source=None,
            source_designer_raw="Undercover",
            source_title="Undercover Jacket",
            source_weight_grams=900,
            ingest_mode="sync",
            url=f"https://example.com/{product_id}",
            images=[],
            variants=[],
        )
        return SimpleNamespace(
            id=product_id,
            designer=SimpleNamespace(name="Undercover"),
            primary_listing=listing,
            price_override=None,
            weight_rule=None,
            presentation=None,
            visibility_status="visible",
            manual_weight_grams=None,
        )

    service = DedupServiceV2(db=None)  # type: ignore[arg-type]
    service.products = _FakeProductsRepo(
        [
            _product(1),
            _product(10),
            _product(20),
        ]
    )
    service.decisions = _FakeDecisionsRepo(
        [
            SimpleNamespace(left_product_id=10, right_product_id=20, score=0.99, reasons=["same_title"]),
            SimpleNamespace(left_product_id=1, right_product_id=20, score=0.99, reasons=["same_title"]),
            SimpleNamespace(left_product_id=1, right_product_id=10, score=0.99, reasons=["same_title"]),
        ]
    )

    result = service.list_candidates(limit=10, offset=0)

    assert result["total"] == 3
    assert [item["pair_key"] for item in result["items"]] == [
        "10:20",
        "1:20",
        "1:10",
    ]


def test_can_undo_merge_is_blocked_when_created_product_used_later() -> None:
    decision = SimpleNamespace(
        id=77,
        decision_kind="merge",
        created_product_id=20,
        undo_payload={
            "listing_owners": [
                {"listing_id": 101, "product_id": 1},
                {"listing_id": 102, "product_id": 2},
            ]
        },
    )
    service = DedupServiceV2(db=None)  # type: ignore[arg-type]
    service.products = _UndoFakeProductsRepo()
    service.decisions = _UndoFakeDecisionsRepo(decision, has_dependents=True)

    can_undo, reason = service.can_undo_decision(decision)

    assert can_undo is False
    assert reason == "Есть более новые решения, зависящие от этого объединения"


def test_can_undo_merge_is_blocked_when_listing_set_changed() -> None:
    decision = SimpleNamespace(
        id=78,
        decision_kind="merge",
        created_product_id=20,
        undo_payload={
            "listing_owners": [
                {"listing_id": 101, "product_id": 1},
                {"listing_id": 102, "product_id": 2},
            ]
        },
    )
    service = DedupServiceV2(db=None)  # type: ignore[arg-type]
    service.products = _UndoFakeProductsRepo(current_listing_ids=[101, 999])
    service.decisions = _UndoFakeDecisionsRepo(decision, has_dependents=False)

    can_undo, reason = service.can_undo_decision(decision)

    assert can_undo is False
    assert reason == "Состав объединенного товара уже изменился"


def test_undo_reject_deletes_decision() -> None:
    decision = SimpleNamespace(id=79, decision_kind="reject", created_product_id=None, undo_payload=None)
    decisions = _UndoFakeDecisionsRepo(decision, has_dependents=False)
    service = DedupServiceV2(db=None)  # type: ignore[arg-type]
    service.products = _UndoFakeProductsRepo()
    service.decisions = decisions

    service.undo(decision_id=79)

    assert decisions.deleted_ids == [79]


def test_undo_latest_merge_restores_previous_merge_memberships() -> None:
    previous_merge_decision = SimpleNamespace(
        id=13,
        decision_kind="merge",
        created_product_id=20,
        undo_payload={
            "listing_owners": [
                {"listing_id": 101, "product_id": 1},
                {"listing_id": 102, "product_id": 2},
            ]
        },
    )
    latest_merge_decision = SimpleNamespace(
        id=14,
        decision_kind="merge",
        created_product_id=30,
        undo_payload={
            "products": [
                {"product_id": 20, "lifecycle_status": "active", "primary_listing_id": 101},
                {"product_id": 3, "lifecycle_status": "merged", "primary_listing_id": 103},
            ],
            "listing_owners": [
                {"listing_id": 101, "product_id": 20},
                {"listing_id": 102, "product_id": 20},
                {"listing_id": 103, "product_id": 3},
            ],
            "gallery_scopes": [],
        },
    )

    class _ChainUndoDecisionsRepo(_UndoFakeDecisionsRepo):
        def __init__(self) -> None:
            super().__init__(latest_merge_decision, has_dependents=False)
            self.dependent_links = {20: {14}, 30: set()}

        def get_decision(self, decision_id: int):
            if int(decision_id) == 14:
                return latest_merge_decision
            if int(decision_id) == 13:
                return previous_merge_decision
            return None

        def has_dependent_decisions(self, *, created_product_id: int, exclude_decision_id: int) -> bool:
            linked_decision_ids = set(self.dependent_links.get(int(created_product_id), set()))
            linked_decision_ids.discard(int(exclude_decision_id))
            return bool(linked_decision_ids)

        def delete_decision(self, decision) -> None:
            super().delete_decision(decision)
            deleted_decision_id = int(decision.id)
            for linked_decision_ids in self.dependent_links.values():
                linked_decision_ids.discard(deleted_decision_id)

    service = DedupServiceV2(db=_ChainUndoFakeDb())  # type: ignore[arg-type]
    service.products = _ChainUndoFakeProductsRepo()
    service.decisions = _ChainUndoDecisionsRepo()

    service.undo(decision_id=14)

    assert service.products.membership_by_listing_id == {
        101: 20,
        102: 20,
        103: 3,
    }
    can_undo, reason = service.can_undo_decision(previous_merge_decision)
    assert can_undo is True
    assert reason is None
