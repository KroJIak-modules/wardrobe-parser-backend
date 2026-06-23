from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.models import Filter, FilterLocalCategoryKeyword, FilterTitleKeyword, Product, ProductListing, Source, SourceSetting
from app.services.catalog.filter_assignment_service import ProductFilterAssignmentService
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.product_query_service import ProductQueryService, UNMATCHED_FILTER_LABEL, UNMATCHED_FILTER_SLUG


def _create_source(db, source_key: str) -> Source:
    source = Source(
        key=source_key,
        name=source_key,
        base_url=f"https://{source_key}",
        base_url_normalized=source_key,
        host_normalized=source_key,
    )
    db.add(source)
    db.flush()
    db.add(SourceSetting(source_id=int(source.id)))
    db.flush()
    return source


def _ingest_product(
    db,
    *,
    source_id: int,
    source_key: str,
    handle: str,
    title: str,
    category: str,
    tags: list[str],
) -> Product:
    ProductIngestService(db).apply_batch(
        source_id=int(source_id),
        items=[
            {
                "url": f"https://{source_key}/products/{handle}",
                "handle": handle,
                "title": title,
                "description": "",
                "designer": "Test Designer",
                "category": category,
                "tags": tags,
                "gender": "unisex",
                "source_weight_grams": 500,
                "orderability_status": "orderable",
                "variants": [{"title": "UNI", "price": 120.0, "currency": "USD", "available": True}],
                "images": [],
            }
        ],
    )
    db.flush()
    listing = db.query(ProductListing).filter(ProductListing.source_id == int(source_id), ProductListing.handle == handle).one()
    return db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()


def _refresh_filter_assignments(db, product_ids: list[int]) -> None:
    service = ProductFilterAssignmentService(db)
    state = service.assignments.get_or_create_runtime_state()
    if int(state.applied_revision or 0) <= 0:
        state.target_revision = 1
        state.applied_revision = 1
        db.flush()
    service._replace_revision_assignments_for_product_ids(  # noqa: SLF001 - targeted integration test helper
        revision=int(state.applied_revision),
        product_ids=product_ids,
    )
    db.flush()


def test_local_category_keywords_match_product_tags_in_addition_to_local_category() -> None:
    db = SessionLocal()
    source_key = f"tag-match-{uuid4().hex[:12]}.example"
    filter_slug = f"tag-match-{uuid4().hex[:12]}"
    tag_keyword = f"tag-{uuid4().hex}"
    try:
        source = _create_source(db, source_key)
        product = _ingest_product(
            db,
            source_id=int(source.id),
            source_key=source_key,
            handle="test-item",
            title="Tag Signal Artifact",
            category="Experimental category",
            tags=[tag_keyword, "carry-over"],
        )

        filter_entity = Filter(
            title="Tag match filter",
            display_title=None,
            slug=filter_slug,
            node_kind="filter",
            is_enabled=True,
        )
        db.add(filter_entity)
        db.flush()
        db.add(FilterLocalCategoryKeyword(filter_id=int(filter_entity.id), keyword=tag_keyword))
        db.flush()
        _refresh_filter_assignments(db, [int(product.id)])

        service = ProductQueryService(db)
        assert filter_slug in service._matched_filter_slugs(product)

        payload = service.list_products(limit=10, offset=0, filter_slug=filter_slug, audience="admin")
        product_ids = [int(item["id"]) for item in payload["items"]]

        assert int(product.id) in product_ids
        assert int(payload["total"]) >= 1
    finally:
        db.rollback()
        db.close()


def test_best_filter_assignment_uses_filter_with_more_matched_keywords() -> None:
    db = SessionLocal()
    source_key = f"best-filter-{uuid4().hex[:12]}.example"
    slug_one = f"best-filter-a-{uuid4().hex[:8]}"
    slug_two = f"best-filter-b-{uuid4().hex[:8]}"
    keyword_one = f"kw-{uuid4().hex[:8]}"
    keyword_two = f"kw-{uuid4().hex[:8]}"
    try:
        source = _create_source(db, source_key)
        product = _ingest_product(
            db,
            source_id=int(source.id),
            source_key=source_key,
            handle="best-item",
            title="Signal Module",
            category="Experimental category",
            tags=[keyword_one, keyword_two],
        )

        filter_one = Filter(title="One hit", slug=slug_one, node_kind="filter", is_enabled=True)
        filter_two = Filter(title="Two hits", slug=slug_two, node_kind="filter", is_enabled=True)
        db.add_all([filter_one, filter_two])
        db.flush()
        db.add_all(
            [
                FilterLocalCategoryKeyword(filter_id=int(filter_one.id), keyword=keyword_one),
                FilterLocalCategoryKeyword(filter_id=int(filter_two.id), keyword=keyword_one),
                FilterLocalCategoryKeyword(filter_id=int(filter_two.id), keyword=keyword_two),
            ]
        )
        db.flush()
        _refresh_filter_assignments(db, [int(product.id)])

        service = ProductQueryService(db)
        assert service._matched_filter_slugs(product) == [slug_two]

        payload = service.list_admin_table_products(limit=10, offset=0, source_id=int(source.id), filter_slug=slug_two)
        assert [int(item["id"]) for item in payload["items"]] == [int(product.id)]

        payload_other = service.list_admin_table_products(limit=10, offset=0, source_id=int(source.id), filter_slug=slug_one)
        assert payload_other["total"] == 0
    finally:
        db.rollback()
        db.close()


def test_without_filters_bucket_contains_only_products_without_assigned_filter() -> None:
    db = SessionLocal()
    source_key = f"without-filter-{uuid4().hex[:12]}.example"
    filter_slug = f"outerwear-{uuid4().hex[:8]}"
    try:
        source = _create_source(db, source_key)
        matched_product = _ingest_product(
            db,
            source_id=int(source.id),
            source_key=source_key,
            handle="with-filter",
            title="Structured Coat",
            category="Outerwear",
            tags=["tailored"],
        )
        unmatched_product = _ingest_product(
            db,
            source_id=int(source.id),
            source_key=source_key,
            handle="without-filter",
            title="Plain Item",
            category="Misc",
            tags=["plain"],
        )

        filter_entity = Filter(title="Outerwear", slug=filter_slug, node_kind="filter", is_enabled=True)
        db.add(filter_entity)
        db.flush()
        db.add(FilterLocalCategoryKeyword(filter_id=int(filter_entity.id), keyword="outerwear"))
        db.flush()
        _refresh_filter_assignments(db, [int(matched_product.id), int(unmatched_product.id)])

        service = ProductQueryService(db)
        no_filter_payload = service.list_admin_table_products(limit=10, offset=0, filter_slug=UNMATCHED_FILTER_SLUG)
        no_filter_ids = [int(item["id"]) for item in no_filter_payload["items"]]
        assert int(unmatched_product.id) in no_filter_ids
        assert int(matched_product.id) not in no_filter_ids

        facets = service.admin_table_facets()
        no_filter_option = next(item for item in facets["sections"] if str(item["value"]) == UNMATCHED_FILTER_SLUG)
        assert no_filter_option["label"] == UNMATCHED_FILTER_LABEL
        assert int(no_filter_option["count"]) >= 1

        filtered_facets = service.admin_table_facets(filter_slug=UNMATCHED_FILTER_SLUG)
        assert int(filtered_facets["total"]) == int(no_filter_payload["total"])
    finally:
        db.rollback()
        db.close()


def test_filter_keyword_matches_whole_token_not_substring_inside_word() -> None:
    db = SessionLocal()
    source_key = f"token-boundary-{uuid4().hex[:12]}.example"
    top_slug = f"tops-{uuid4().hex[:8]}"
    boots_slug = f"boots-{uuid4().hex[:8]}"
    category_keyword_one = f"boundary-{uuid4().hex[:8]}"
    category_keyword_two = f"boots-{uuid4().hex[:8]}"
    try:
        source = _create_source(db, source_key)
        product = _ingest_product(
            db,
            source_id=int(source.id),
            source_key=source_key,
            handle="m-overztank001-c1",
            title="M-OVERZTANK001-C1",
            category=f"{category_keyword_one} {category_keyword_two}",
            tags=[],
        )

        top_filter = Filter(title="Tops", slug=top_slug, node_kind="filter", is_enabled=True)
        boots_filter = Filter(title="Boots", slug=boots_slug, node_kind="filter", is_enabled=True)
        db.add_all([top_filter, boots_filter])
        db.flush()
        db.add_all(
            [
                FilterTitleKeyword(filter_id=int(top_filter.id), keyword="tank"),
                FilterLocalCategoryKeyword(filter_id=int(boots_filter.id), keyword=category_keyword_one),
                FilterLocalCategoryKeyword(filter_id=int(boots_filter.id), keyword=category_keyword_two),
            ]
        )
        db.flush()
        _refresh_filter_assignments(db, [int(product.id)])

        service = ProductQueryService(db)
        assert service._matched_filter_slugs(product) == [boots_slug]

        boots_payload = service.list_admin_table_products(limit=10, offset=0, filter_slug=boots_slug)
        assert [int(item["id"]) for item in boots_payload["items"]] == [int(product.id)]

        tops_payload = service.list_admin_table_products(limit=10, offset=0, filter_slug=top_slug)
        assert int(tops_payload["total"]) == 0
    finally:
        db.rollback()
        db.close()
