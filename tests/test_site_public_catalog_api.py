from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.core.database import SessionLocal
from app.models import Designer, ImageAsset, Product, ProductListing
from app.services.catalog.product_query_service import ProductQueryService
from app.schemas.taxonomy import TaxonomyFilterNode, TaxonomyState
from app.services.catalog.product_write_service import ProductWriteService
from app.services.catalog.designer_support import slugify_designer_name
from app.services.catalog.site_query_service import SiteQueryService


def _create_image_asset_id(db, marker: str, slug: str) -> int:
    asset = ImageAsset(
        scope="products",
        storage_key=f"products/tests/site-public-{marker}-{slug}.jpg",
        mime_type="image/jpeg",
        byte_size=128,
        width_px=80,
        height_px=80,
        checksum_sha256=f"{marker}{slug}".ljust(64, "0")[:64],
    )
    db.add(asset)
    db.flush()
    return int(asset.id)


def _create_manual_public_product(
    db,
    *,
    marker: str,
    slug: str,
    price: int,
    compare_at_price: int | None = None,
    available: bool = True,
    gender: str = "unisex",
    variants: list[dict] | None = None,
) -> int:
    image_asset_id = _create_image_asset_id(db, marker, slug)
    return ProductWriteService(db).create_manual_product(
        {
            "title": f"Site public {marker} {slug}",
            "description_text": "Public site test item",
            "designer_name": f"Site public designer {marker}",
            "source_category_name": "Outerwear",
            "gender": gender,
            "availability_mode": "in_stock" if available else "by_order",
            "visibility_status": "visible",
            "orderability_status": "orderable" if available else "sold_out",
            "manual_weight_grams": 500,
            "variants": variants
            if variants is not None
            else [
                {
                    "title": "Default",
                    "price": price,
                    "compare_at_price": compare_at_price,
                    "currency": "RUB",
                    "available": available,
                }
            ],
            "manual_image_asset_ids": [image_asset_id],
            "filter_slugs": [],
            "custom_catalog_slugs": [],
        }
    )


def test_site_catalog_defaults_to_orderable_and_can_filter_discounted_only() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    try:
        regular_id = _create_manual_public_product(db, marker=marker, slug="regular", price=12000)
        discounted_id = _create_manual_public_product(
            db,
            marker=marker,
            slug="discounted",
            price=9000,
            compare_at_price=14000,
        )
        sold_out_id = _create_manual_public_product(
            db,
            marker=marker,
            slug="sold-out",
            price=7000,
            available=False,
        )

        service = SiteQueryService(db)

        default_payload = service.catalog_products(
            limit=10,
            offset=0,
            query=marker,
            designer_slugs=[],
            gender_values=[],
            filter_slugs=[],
            custom_catalog_slug=None,
            availability_mode=None,
            orderability_status=None,
            discounted_only=False,
        )
        sold_out_payload = service.catalog_products(
            limit=10,
            offset=0,
            query=marker,
            designer_slugs=[],
            gender_values=[],
            filter_slugs=[],
            custom_catalog_slug=None,
            availability_mode=None,
            orderability_status="sold_out",
            discounted_only=False,
        )
        discounted_payload = service.catalog_products(
            limit=10,
            offset=0,
            query=marker,
            designer_slugs=[],
            gender_values=[],
            filter_slugs=[],
            custom_catalog_slug=None,
            availability_mode=None,
            orderability_status=None,
            discounted_only=True,
        )
        designer_slug = slugify_designer_name(f"Site public designer {marker}")
        designer_payload = service.catalog_products(
            limit=10,
            offset=0,
            query=marker,
            designer_slugs=[designer_slug],
            gender_values=[],
            filter_slugs=[],
            custom_catalog_slug=None,
            availability_mode=None,
            orderability_status=None,
            discounted_only=False,
        )

        assert {item.id for item in default_payload.items} == {regular_id, discounted_id}
        assert all(item.status != "sold_out" for item in default_payload.items)

        assert {item.id for item in sold_out_payload.items} == {sold_out_id}
        assert all(item.status == "sold_out" for item in sold_out_payload.items)

        assert {item.id for item in discounted_payload.items} == {discounted_id}
        assert discounted_payload.total == 1

        assert {item.id for item in designer_payload.items} == {regular_id, discounted_id}

        experience_payload = service.catalog_experience(view_key="designers", search_params={})
        designer_group = next(group for group in experience_payload.filter_groups if group.key == "designer")
        matching_option = next(option for option in designer_group.options if option.label == f"Site public designer {marker}")
        assert matching_option.value == designer_slug
        assert not matching_option.value.isdigit()

        directory_payload = service.designers()
        matching_directory_entry = next(entry for entry in directory_payload.entries if entry.label == f"Site public designer {marker}")
        assert matching_directory_entry.slug == designer_slug

        db_slug = db.query(Designer.slug).filter(Designer.slug == designer_slug).scalar()
        assert db_slug == designer_slug
    finally:
        db.rollback()
        db.close()


def test_site_catalog_gender_mapping_excludes_unisex_from_women() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    try:
        male_id = _create_manual_public_product(db, marker=marker, slug="male", price=12000, gender="male")
        female_id = _create_manual_public_product(db, marker=marker, slug="female", price=13000, gender="female")
        unisex_id = _create_manual_public_product(db, marker=marker, slug="unisex", price=14000, gender="unisex")

        service = SiteQueryService(db)
        men_payload = service.catalog_products(
            limit=10,
            offset=0,
            query=marker,
            designer_slugs=[],
            gender_values=["men"],
            filter_slugs=[],
            custom_catalog_slug=None,
            availability_mode=None,
            orderability_status=None,
            discounted_only=False,
        )
        women_payload = service.catalog_products(
            limit=10,
            offset=0,
            query=marker,
            designer_slugs=[],
            gender_values=["women"],
            filter_slugs=[],
            custom_catalog_slug=None,
            availability_mode=None,
            orderability_status=None,
            discounted_only=False,
        )

        assert {item.id for item in men_payload.items} == {male_id, unisex_id}
        assert {item.id for item in women_payload.items} == {female_id}
    finally:
        db.rollback()
        db.close()


def test_public_product_excludes_non_orderable_variants() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    try:
        product_id = _create_manual_public_product(
            db,
            marker=marker,
            slug="variants",
            price=12000,
            variants=[
                {"title": "S", "price": 12000, "currency": "RUB", "available": True},
                {"title": "M", "price": 13000, "currency": "RUB", "available": False},
            ],
        )

        payload = SiteQueryService(db).product(f"{product_id}-public-variants")

        assert [variant.size for variant in payload.variants] == ["S"]
    finally:
        db.rollback()
        db.close()


def test_public_product_converts_html_description_field_to_text() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    try:
        product_id = _create_manual_public_product(db, marker=marker, slug="legacy-html", price=12000)
        product = db.query(Product).filter(Product.id == int(product_id)).one()
        listing = db.query(ProductListing).filter(ProductListing.id == int(product.primary_listing_id or 0)).one()
        listing.source_description_text = None
        listing.source_description_html = "<p>Первый <strong>абзац</strong></p><p>Второй&nbsp;абзац</p>"
        db.flush()

        payload = SiteQueryService(db).product(f"{product_id}-legacy-html")

        assert payload.description is not None
        assert payload.description.format == "text"
        assert payload.description.content == "Первый абзац\nВторой абзац"
    finally:
        db.rollback()
        db.close()


def test_public_title_cleaning_is_source_scoped_and_keeps_original_title_searchable() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    try:
        product_id = _create_manual_public_product(db, marker=marker, slug="title-cleaning", price=12000)
        product = db.query(Product).filter(Product.id == int(product_id)).one()
        listing = db.query(ProductListing).filter(ProductListing.id == int(product.primary_listing_id or 0)).one()
        listing.source_title = "DS01F4726_DWB_06 | KUNST JKT"
        listing.source.setting.clean_public_titles = True
        db.flush()

        service = SiteQueryService(db)
        public_product = service.product(f"{product_id}-title-cleaning")
        search_result = service.catalog_products(
            limit=10,
            offset=0,
            query="DS01F4726_DWB_06",
            designer_slugs=[],
            gender_values=[],
            filter_slugs=[],
            custom_catalog_slug=None,
            availability_mode=None,
            orderability_status=None,
            discounted_only=False,
        )

        assert public_product.name == "KUNST JKT"
        assert [item.id for item in search_result.items] == [product_id]
        assert search_result.items[0].name == "KUNST JKT"

        listing.source.setting.clean_public_titles = False
        db.flush()

        uncleaned_product = service.product(f"{product_id}-title-cleaning")
        assert uncleaned_product.name == "DS01F4726_DWB_06 | KUNST JKT"
    finally:
        db.rollback()
        db.close()


def test_site_catalog_products_use_lightweight_card_query() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    try:
        product_id = _create_manual_public_product(
            db,
            marker=marker,
            slug="light-card",
            price=16500,
            compare_at_price=22000,
        )
        product = db.query(Product).filter(Product.id == int(product_id)).one()
        product.site_sort_price_rub = 16500
        db.flush()

        service = SiteQueryService(db)

        def _fail_build_public_payload(*args, **kwargs):
            raise AssertionError("catalog_products should not build full public payloads for catalog cards")

        service.products.build_public_product_payload = _fail_build_public_payload  # type: ignore[method-assign]

        payload = service.catalog_products(
            limit=10,
            offset=0,
            query=marker,
            designer_slugs=[],
            gender_values=[],
            filter_slugs=[],
            custom_catalog_slug=None,
            availability_mode=None,
            orderability_status=None,
            discounted_only=False,
        )

        assert payload.total == 1
        assert payload.items[0].id == product_id
        assert payload.items[0].price_rub == 16500
        assert payload.items[0].status == "in_stock"
        assert payload.items[0].brand.name == f"Site public designer {marker}"
        assert str(payload.items[0].image_url or "").startswith("/api/v1/products/images/")
    finally:
        db.rollback()
        db.close()


def test_site_catalog_brand_never_uses_source_designer_name() -> None:
    row = SimpleNamespace(
        designer_name="Final Designer",
        source_designer_raw="Source Designer",
        brand_override_name="Manual Source Label",
    )

    assert SiteQueryService._site_catalog_brand_name(row) == "Final Designer"


def test_public_product_payload_excludes_source_designer_name() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    try:
        product_id = _create_manual_public_product(db, marker=marker, slug="designer-contract", price=12000)
        product = db.query(Product).filter(Product.id == product_id).one()
        listing = db.query(ProductListing).filter(ProductListing.id == product.primary_listing_id).one()
        listing.source_designer_raw = "Unpublished Source Designer"
        db.flush()

        payload = ProductQueryService(db).build_public_product_payload(product)

        assert payload["brand_name"] == f"Site public designer {marker}"
        assert payload["display_designer_name"] == f"Site public designer {marker}"
        assert "source_designer_name" not in payload
        assert "Unpublished Source Designer" not in str(payload)
    finally:
        db.rollback()
        db.close()


def test_site_navigation_mobile_menu_uses_filter_title_not_display_title() -> None:
    db = SessionLocal()
    try:
        service = SiteQueryService(db)
        service.taxonomy.get_state = lambda: TaxonomyState(  # type: ignore[method-assign]
            filters=[
                TaxonomyFilterNode(
                    slug="clothes",
                    title="Одежда",
                    display_title="Коротко одежда",
                    node_kind="multifilter",
                    mobile_pair_slug="shoes",
                    children=[
                        TaxonomyFilterNode(
                            slug="tops",
                            title="Верх",
                            display_title="Коротко верх",
                            node_kind="multifilter",
                            children=[
                                TaxonomyFilterNode(
                                    slug="shirts",
                                    title="Рубашки",
                                    display_title="Рубашки и блузы",
                                    node_kind="filter",
                                )
                            ],
                        )
                    ],
                ),
                TaxonomyFilterNode(
                    slug="shoes",
                    title="Обувь",
                    display_title="Коротко обувь",
                    node_kind="multifilter",
                    mobile_pair_slug="clothes",
                    children=[
                        TaxonomyFilterNode(
                            slug="boots",
                            title="Ботинки",
                            display_title="Коротко ботинки",
                            node_kind="filter",
                        )
                    ],
                ),
            ],
            custom_catalogs=[],
            showcase_categories=[],
        )

        payload = service.navigation()
        group = payload.mobile_menu.root_groups[0]

        assert group.label == "Одежда и Обувь"
        assert [item.label for item in group.root_multi_filters] == ["Одежда", "Обувь"]
        assert group.children[0].multi_filter.label == "Верх"
        assert group.children[0].sections[0].label == "Рубашки"
        assert group.children[1].multi_filter.label == "Ботинки"
    finally:
        db.rollback()
        db.close()


def test_site_catalog_filter_bar_uses_display_label_for_sections() -> None:
    db = SessionLocal()
    try:
        service = SiteQueryService(db)
        service.preview._taxonomy_state = {
            "filters": [
                {
                    "id": 10,
                    "slug": "clothes",
                    "label": "Одежда",
                    "display_label": "Не для меню",
                    "is_enabled": True,
                    "children": [
                        {
                            "id": 11,
                            "slug": "shirts",
                            "label": "Рубашки",
                            "display_label": "Рубашки и блузы",
                            "is_enabled": True,
                            "children": [],
                        },
                        {
                            "id": 12,
                            "slug": "pants",
                            "label": "Брюки",
                            "display_label": None,
                            "is_enabled": True,
                            "children": [],
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
                            "hidden_node_ids": [12],
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
                            "ref_id": 10,
                            "hidden_node_ids": [11],
                        }
                    ],
                    "children": [],
                },
            ],
            "custom_catalogs": [],
            "designer_directory": [],
        }

        payload = service.catalog_experience(view_key="default", search_params={"gender": ["men"]})
        section_group = next(group for group in payload.filter_groups if group.key == "section")

        assert [(option.label, option.value) for option in section_group.options] == [
            ("Рубашки и блузы", "shirts"),
            ("Брюки", "pants"),
        ]
    finally:
        db.rollback()
        db.close()


def test_site_catalog_experience_infers_gender_from_top_when_gender_param_missing() -> None:
    db = SessionLocal()
    try:
        service = SiteQueryService(db)
        service.preview._taxonomy_state = {
            "filters": [
                {
                    "id": 10,
                    "slug": "clothes",
                    "label": "Одежда",
                    "display_label": None,
                    "is_enabled": True,
                    "children": [
                        {
                            "id": 11,
                            "slug": "male-shirts",
                            "label": "Мужские рубашки",
                            "display_label": None,
                            "is_enabled": True,
                            "children": [],
                        },
                        {
                            "id": 12,
                            "slug": "female-dresses",
                            "label": "Женские платья",
                            "display_label": None,
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
                            "id": "attachment-men",
                            "kind": "filter",
                            "ref_id": 10,
                            "hidden_node_ids": [12],
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
                            "id": "attachment-women",
                            "kind": "filter",
                            "ref_id": 10,
                            "hidden_node_ids": [11],
                        }
                    ],
                    "children": [],
                },
            ],
            "custom_catalogs": [],
            "designer_directory": [],
        }

        payload = service.catalog_experience(view_key="default", search_params={"top": ["women"]})
        section_group = next(group for group in payload.filter_groups if group.key == "section")

        assert [option.value for option in section_group.options] == ["female-dresses", "male-shirts"]
    finally:
        db.rollback()
        db.close()


def test_site_desktop_menu_keeps_taxonomy_order_and_uses_filter_title() -> None:
    db = SessionLocal()
    try:
        service = SiteQueryService(db)
        service.preview._taxonomy_state = {
            "filters": [
                {
                    "id": 10,
                    "slug": "clothes",
                    "label": "Одежда",
                    "display_label": "Не для меню",
                    "is_enabled": True,
                    "children": [
                        {
                            "id": 11,
                            "slug": "top",
                            "label": "Верх",
                            "display_label": "Коротко верх",
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
                        },
                        {
                            "id": 13,
                            "slug": "outerwear",
                            "label": "Верхняя одежда",
                            "display_label": "Коротко верхняя",
                            "is_enabled": True,
                            "children": [],
                        },
                        {
                            "id": 14,
                            "slug": "bottoms",
                            "label": "Низ",
                            "display_label": "Коротко низ",
                            "is_enabled": True,
                            "children": [
                                {
                                    "id": 15,
                                    "slug": "shorts",
                                    "label": "Шорты",
                                    "display_label": "Коротко шорты",
                                    "is_enabled": True,
                                    "children": [],
                                }
                            ],
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

        payload = service.navigation()
        column = payload.desktop_menus["men"].columns[0]
        entries = column.entries

        assert column.title is not None
        assert column.title.label == "Одежда"
        assert column.title.target is not None
        assert column.title.target.pathname == "/catalog"
        assert column.title.target.query == {
            "section": ["shirts", "outerwear", "shorts"],
            "gender": "men",
            "ctx": "menu_filter",
            "ctx_ref": "attachment-1:10",
        }

        assert [entry.label for entry in entries] == ["Верх", "Рубашки", "Верхняя одежда", "Низ", "Шорты"]
        assert [entry.presentation for entry in entries] == ["heading", "item", "item", "heading", "item"]
        assert entries[0].target is not None
        assert entries[0].target.pathname == "/catalog"
        assert entries[0].target.query == {
            "section": ["shirts"],
            "gender": "men",
            "ctx": "menu_filter",
            "ctx_ref": "attachment-1:11",
        }
        assert entries[3].target is not None
        assert entries[3].target.query == {
            "section": ["shorts"],
            "gender": "men",
            "ctx": "menu_filter",
            "ctx_ref": "attachment-1:14",
        }
    finally:
        db.rollback()
        db.close()
