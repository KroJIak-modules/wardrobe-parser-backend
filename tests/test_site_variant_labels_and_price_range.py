from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.models import ImageAsset
from app.services.catalog.site_catalog_sort_price_service import SiteCatalogSortPriceService
from app.services.catalog.site_query_service import SiteQueryService
from app.services.catalog.product_write_service import ProductWriteService


def _create_image_asset_id(db, marker: str, slug: str) -> int:
    asset = ImageAsset(
        scope="products",
        storage_key=f"products/tests/site-range-{marker}-{slug}.jpg",
        mime_type="image/jpeg",
        byte_size=128,
        width_px=80,
        height_px=80,
        checksum_sha256=f"{marker}{slug}".ljust(64, "0")[:64],
    )
    db.add(asset)
    db.flush()
    return int(asset.id)


def _create_product_with_variants(db, *, marker: str, slug: str, variants: list[dict]) -> int:
    image_asset_id = _create_image_asset_id(db, marker, slug)
    product_id = ProductWriteService(db).create_manual_product(
        {
            "title": f"Site range {marker} {slug}",
            "description_text": "Price range test item",
            "designer_name": f"Site range designer {marker}",
            "source_category_name": "Outerwear",
            "gender": "unisex",
            "availability_mode": "in_stock",
            "visibility_status": "visible",
            "orderability_status": "orderable",
            "manual_weight_grams": 500,
            "variants": variants,
            "manual_image_asset_ids": [image_asset_id],
            "filter_slugs": [],
            "custom_catalog_slugs": [],
        }
    )
    return int(product_id)


def test_size_label_keeps_size_for_plain_sizes() -> None:
    assert SiteQueryService._size_label(["S", "M", "L"]) == "РАЗМЕР"
    assert SiteQueryService._size_label(["36", "38", "40"]) == "РАЗМЕР"
    assert SiteQueryService._size_label(["ONE SIZE"]) == "РАЗМЕР"
    assert SiteQueryService._size_label(["0 / 3XS", "1 / 2XS", "6 / XL"]) == "РАЗМЕР"
    assert SiteQueryService._size_label([]) == "РАЗМЕР"


def test_size_label_switches_to_variants_for_models_and_combinations() -> None:
    # Модели и бандл вместо размеров.
    assert SiteQueryService._size_label(["X-T Coins", "X-T D Ring Mini", "BUNDLE"]) == "ВАРИАНТЫ"
    # Размер + кастомизация в одной строке.
    assert SiteQueryService._size_label(["0 / 3XS / STD", "1 / 2XS / Normal"]) == "ВАРИАНТЫ"
    # Размер + цвет + кастомизация.
    assert SiteQueryService._size_label(["0 / 3XS / Black / By Teruki"]) == "ВАРИАНТЫ"
    # Цвет и размер вперемешку.
    assert SiteQueryService._size_label(["M / Black", "L / Black"]) == "ВАРИАНТЫ"
    # Смешанные размерные системы.
    assert SiteQueryService._size_label(["M", "L", "38"]) == "ВАРИАНТЫ"


def test_prices_have_range_detects_different_prices() -> None:
    assert SiteQueryService._prices_have_range([25800.0, 57000.0]) is True
    assert SiteQueryService._prices_have_range([25800.0, 25800.0]) is False
    assert SiteQueryService._prices_have_range([25800.0, 25800.005]) is False
    assert SiteQueryService._prices_have_range([25800.0]) is False
    assert SiteQueryService._prices_have_range([None]) is False


def test_site_product_reports_price_range_and_size_label() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    try:
        product_id = _create_product_with_variants(
            db,
            marker=marker,
            slug="variants",
            variants=[
                {"title": "X-T Coins", "price": 57000, "currency": "RUB", "available": True},
                {"title": "X-T D Ring Mini", "price": 25800, "currency": "RUB", "available": True},
                {"title": "BUNDLE", "price": 74700, "currency": "RUB", "available": True},
            ],
        )
        db.commit()

        payload = SiteQueryService(db).product(f"{product_id}-site-range")
        assert payload.price_has_range is True
        assert payload.size_label == "ВАРИАНТЫ"

        same_price_payload = SiteQueryService(db)
        assert same_price_payload._size_label(["S", "M"]) == "РАЗМЕР"
    finally:
        db.rollback()
        db.close()


def test_site_product_without_price_range_has_no_prefix_flag() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    try:
        product_id = _create_product_with_variants(
            db,
            marker=marker,
            slug="single-price",
            variants=[
                {"title": "S", "price": 25800, "currency": "RUB", "available": True},
                {"title": "M", "price": 25800, "currency": "RUB", "available": True},
            ],
        )
        db.commit()

        payload = SiteQueryService(db).product(f"{product_id}-site-range")
        assert payload.price_has_range is False
        assert payload.size_label == "РАЗМЕР"
    finally:
        db.rollback()
        db.close()


def test_site_catalog_card_reports_price_range() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    try:
        product_id = _create_product_with_variants(
            db,
            marker=marker,
            slug="catalog-range",
            variants=[
                {"title": "S", "price": 25800, "currency": "RUB", "available": True},
                {"title": "M", "price": 74700, "currency": "RUB", "available": True},
            ],
        )
        db.commit()

        SiteCatalogSortPriceService(db).refresh_product_ids([product_id])
        db.commit()

        payload = SiteQueryService(db).catalog_products(
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
        assert payload.items, "товар должен попасть в выдачу витрины"
        item = next(entry for entry in payload.items if entry.id == product_id)
        assert item.price_rub == 25800
        assert item.price_has_range is True
    finally:
        db.rollback()
        db.close()
