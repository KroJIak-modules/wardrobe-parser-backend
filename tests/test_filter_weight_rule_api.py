from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.v1.auth as auth_module
from app.core.database import SessionLocal
from app.main import app
from app.models import (
    Filter,
    FilterAssignmentRuntimeState,
    Product,
    ProductFilterAssignment,
    ProductListing,
    ProductListingMember,
    Source,
    SourceSetting,
    WeightRule,
    WeightRuleKeyword,
)
from app.schemas.admin_settings import WeightRecalcStatusResponse, WeightRuleCreateRequest, WeightRuleKeywordRequest
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.taxonomy_service import TaxonomyService
from app.schemas.taxonomy import TaxonomyFilterNode, TaxonomyState
from app.services.settings.weight_recalc_runtime_service import WeightRecalcRuntimeService
from app.services.settings.weight_recalc_queue import WeightRuleRecalcQueue
from app.services.settings.weight_rule_service import WeightRuleService
from app.workers.bybit_rate_worker import _run_weight_recalc_once
import app.workers.bybit_rate_worker as weight_worker_module


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
    response = client.post("/api/v1/auth/login", json={"login": "superadmin", "password": "Q7m2Lx9pRt"})
    assert response.status_code == 200
    return client


def _create_source(db, marker: str) -> Source:
    source = Source(
        key=f"filter-weight-{marker}.example",
        name=f"Filter Weight {marker}",
        base_url=f"https://filter-weight-{marker}.example",
        base_url_normalized=f"filter-weight-{marker}.example",
    )
    db.add(source)
    db.flush()
    db.add(SourceSetting(source_id=int(source.id), is_enabled=True, is_sync_enabled=True, show_images=True, description_mode="text"))
    db.flush()
    return source


def _create_weight_rule(db, *, weight_grams: int, keywords: list[str] | None = None) -> WeightRule:
    response = WeightRuleService(db).create_rule(WeightRuleCreateRequest(weight_grams=weight_grams))
    entity = db.query(WeightRule).filter(WeightRule.id == int(response.id)).one()
    for keyword in keywords or []:
        WeightRuleService(db).add_keyword(int(entity.id), WeightRuleKeywordRequest(keyword=keyword))
    db.flush()
    return entity


def _custom_taxonomy_state() -> TaxonomyState:
    return TaxonomyState(
        filters=[
            TaxonomyFilterNode(
                slug="bags-root",
                title="Bags Root",
                node_kind="multifilter",
                is_enabled=True,
                children=[
                    TaxonomyFilterNode(
                        slug="bags-test",
                        title="Bags Test",
                        node_kind="filter",
                        is_enabled=True,
                        local_category_keywords=["bags-test"],
                        title_keywords=["bags-test"],
                        children=[],
                    )
                ],
            )
        ],
        custom_catalogs=[],
        showcase_categories=[],
    )


def test_filter_weight_rule_api_updates_mapping_without_immediate_recalculation(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)
    db = SessionLocal()
    taxonomy = TaxonomyService(db)
    original_state = taxonomy.get_state()
    marker = uuid4().hex[:12]
    source_id: int | None = None
    product_id: int | None = None
    listing_id: int | None = None
    previous_rule_id: int | None = None
    bag_rule_id: int | None = None
    try:
        taxonomy.replace_state(_custom_taxonomy_state())
        bag_filter = db.query(Filter).filter(Filter.slug == "bags-test").one()
        previous_rule_id = int(bag_filter.default_weight_rule_id) if bag_filter.default_weight_rule_id is not None else None
        bag_rule = _create_weight_rule(db, weight_grams=2300, keywords=["bag"])
        bag_rule_id = int(bag_rule.id)

        source = _create_source(db, marker)
        source_id = int(source.id)
        handle = f"mystery-piece-{marker}"
        ProductIngestService(db).apply_batch(
            source_id=source_id,
            items=[
                {
                    "url": f"https://filter-weight-{marker}.example/products/{handle}",
                    "handle": handle,
                    "title": "Mystery Piece",
                    "description": "",
                    "designer": "Unknown Designer",
                    "category": "Unknown Category",
                    "tags": ["obscure"],
                    "gender": "unisex",
                    "source_weight_grams": 0,
                    "orderability_status": "orderable",
                    "variants": [{"title": "ONE", "price": 180.0, "currency": "USD", "available": True}],
                    "images": ["https://filter-weight.example/image-1.jpg"],
                }
            ],
        )
        db.flush()
        listing = db.query(ProductListing).filter(ProductListing.source_id == source_id, ProductListing.handle == handle).one()
        product = db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()
        product_id = int(product.id)
        listing_id = int(listing.id)

        state = db.query(FilterAssignmentRuntimeState).filter(FilterAssignmentRuntimeState.id == 1).one_or_none()
        if state is None:
            state = FilterAssignmentRuntimeState(id=1, target_revision=1, applied_revision=1)
            db.add(state)
        else:
            state.target_revision = 1
            state.applied_revision = 1
        db.query(ProductFilterAssignment).filter(ProductFilterAssignment.product_id == product_id).delete(synchronize_session=False)
        db.add(
            ProductFilterAssignment(
                product_id=product_id,
                revision=1,
                filter_slug="bags-test",
                filter_label="Bags Test",
                manual_rank=0,
                match_score=1,
                matched_local_keywords=[],
                matched_title_keywords=[],
            )
        )
        bag_filter.default_weight_rule_id = None
        db.commit()

        db.expire_all()
        persisted_product = db.query(Product).filter(Product.id == product_id).one()
        persisted_listing = db.query(ProductListing).filter(ProductListing.id == listing_id).one()
        assert persisted_product.weight_rule_id is None
        assert persisted_listing.status_reason == "missing_weight"

        response = client.patch(
            "/api/v1/settings/filter-weight-rules",
            json={"items": [{"filter_slug": "bags-test", "weight_rule_id": int(bag_rule.id)}]},
        )

        assert response.status_code == 200
        payload = response.json()
        bag_payload = next(item for item in payload if item["filter_slug"] == "bags-test")
        assert bag_payload["weight_rule_id"] == int(bag_rule.id)
        assert bag_payload["weight_grams"] == 2300

        db.expire_all()
        persisted_product = db.query(Product).filter(Product.id == product_id).one()
        persisted_listing = db.query(ProductListing).filter(ProductListing.id == listing_id).one()
        assert persisted_product.weight_rule_id is None
        assert persisted_listing.status_reason == "missing_weight"
    finally:
        bag_filter = db.query(Filter).filter(Filter.slug == "bags-test").one_or_none()
        if bag_filter is not None:
            bag_filter.default_weight_rule_id = previous_rule_id
        db.query(ProductFilterAssignment).filter(ProductFilterAssignment.product_id == (product_id or 0)).delete(synchronize_session=False)
        if product_id is not None:
            db.query(ProductListingMember).filter(ProductListingMember.product_id == product_id).delete(synchronize_session=False)
            db.query(Product).filter(Product.id == product_id).delete(synchronize_session=False)
        if listing_id is not None:
            db.query(ProductListing).filter(ProductListing.id == listing_id).delete(synchronize_session=False)
        if source_id is not None:
            db.query(SourceSetting).filter(SourceSetting.source_id == source_id).delete(synchronize_session=False)
            db.query(Source).filter(Source.id == source_id).delete(synchronize_session=False)
        if bag_rule_id is not None:
            db.query(WeightRuleKeyword).filter(WeightRuleKeyword.rule_id == bag_rule_id).delete(synchronize_session=False)
            db.query(WeightRule).filter(WeightRule.id == bag_rule_id).delete(synchronize_session=False)
        db.commit()
        taxonomy.replace_state(original_state)
        db.close()


def test_weight_recalculation_endpoint_returns_queued_count(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)
    monkeypatch.setattr(
        WeightRuleService,
        "start_full_recalculation",
        lambda self: (
            7,
            WeightRecalcStatusResponse(
                status="queued",
                is_running=True,
                queued_at="2026-07-02T00:00:00+00:00",
                started_at=None,
                finished_at=None,
                last_error=None,
                total_products=7,
                processed_products=0,
            ),
            True,
        ),
    )
    response = client.post("/api/v1/settings/weight-rules/recalculate")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert int(payload["queued"]) == 7
    assert payload["started"] is True
    assert payload["status"]["status"] == "queued"
    assert payload["status"]["is_running"] is True


def test_weight_recalculation_status_endpoint_returns_persistent_runtime_state(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)
    db = SessionLocal()
    try:
        status_service = WeightRecalcRuntimeService(db)
        status_service.mark_queued(total_products=15)
        response = client.get("/api/v1/settings/weight-rules/recalculate-status")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "queued"
        assert payload["is_running"] is True
        assert payload["total_products"] == 15
        assert payload["processed_products"] == 0
    finally:
        state = status_service.repo.get_or_create_state(for_update=True)
        state.status = "idle"
        state.total_products = 0
        state.processed_products = 0
        state.last_error = None
        state.finished_at = None
        state.started_at = None
        state.queued_at = None
        db.commit()
        db.close()


def test_weight_recalc_worker_marks_runtime_state_finished(monkeypatch) -> None:
    db = SessionLocal()
    queue = WeightRuleRecalcQueue()
    queue._client.delete("catalog:weight-recalc:product-ids")
    monkeypatch.setattr(weight_worker_module.settings, "weight_recalc_worker_debounce_sec", 0)
    monkeypatch.setattr(WeightRuleService, "recalculate_product_ids", lambda self, product_ids: len(list(product_ids)))
    try:
        WeightRecalcRuntimeService(db).mark_queued(total_products=1)
        queue.enqueue_product_ids([987654321])
        processed = _run_weight_recalc_once(batch_size=10)
        assert processed == 1
        db.expire_all()
        status = WeightRecalcRuntimeService(db).serialize()
        assert status.status == "idle"
        assert status.is_running is False
        assert status.total_products == 1
        assert status.processed_products == 1
    finally:
        queue._client.delete("catalog:weight-recalc:product-ids")
        state = WeightRecalcRuntimeService(db).repo.get_or_create_state(for_update=True)
        state.status = "idle"
        state.total_products = 0
        state.processed_products = 0
        state.last_error = None
        state.finished_at = None
        state.started_at = None
        state.queued_at = None
        db.commit()
        db.close()


def test_taxonomy_replace_preserves_filter_weight_mapping_when_editor_payload_has_no_weight_field() -> None:
    db = SessionLocal()
    taxonomy = TaxonomyService(db)
    original_state = taxonomy.get_state()
    original_rule_id: int | None = None
    bag_rule_id: int | None = None
    try:
        taxonomy.replace_state(_custom_taxonomy_state())
        bag_rule = _create_weight_rule(db, weight_grams=2300, keywords=["bag"])
        bag_rule_id = int(bag_rule.id)
        bag_filter = db.query(Filter).filter(Filter.slug == "bags-test").one()
        original_rule_id = int(bag_filter.default_weight_rule_id) if bag_filter.default_weight_rule_id is not None else None
        bag_filter.default_weight_rule_id = int(bag_rule.id)
        db.commit()

        state = taxonomy.get_state()
        for root in state.filters:
            stack = [root]
            while stack:
                node = stack.pop()
                if str(node.slug or "") == "bags-test":
                    node.default_weight_rule_id = None
                stack.extend(list(node.children or []))

        saved = taxonomy.replace_state(state)

        def find_sumki(nodes):
            for node in nodes:
                if str(node.slug or "") == "bags-test":
                    return node
                found = find_sumki(node.children)
                if found is not None:
                    return found
            return None

        saved_node = find_sumki(saved.filters)
        assert saved_node is not None
        assert int(saved_node.default_weight_rule_id or 0) == int(bag_rule.id)

    finally:
        bag_filter = db.query(Filter).filter(Filter.slug == "bags-test").one_or_none()
        if bag_filter is not None:
            bag_filter.default_weight_rule_id = original_rule_id
            db.commit()
        if bag_rule_id is not None:
            db.query(WeightRuleKeyword).filter(WeightRuleKeyword.rule_id == bag_rule_id).delete(synchronize_session=False)
            db.query(WeightRule).filter(WeightRule.id == bag_rule_id).delete(synchronize_session=False)
            db.commit()
        taxonomy.replace_state(original_state)
        db.close()
