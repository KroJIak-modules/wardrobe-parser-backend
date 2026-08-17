from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.models import CustomCatalog, CustomCatalogProduct
from app.services.catalog.admin_showcase_preview_service import AdminShowcasePreviewService
from app.services.catalog.product_write_service import ProductWriteService
from app.services.catalog.site_query_service import SiteQueryService

from test_site_public_catalog_api import _create_manual_public_product


def test_custom_catalog_membership_is_exactly_reflected_in_public_catalog_and_navigation() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:8]
    catalog: CustomCatalog | None = None
    try:
        included_id = _create_manual_public_product(db, marker=marker, slug="included", price=12000)
        excluded_id = _create_manual_public_product(db, marker=marker, slug="excluded", price=13000)
        catalog = CustomCatalog(
            title=f"Selection {marker}",
            slug=f"selection-{marker}",
            description="Тестовый каталог",
            is_enabled=True,
        )
        db.add(catalog)
        db.flush()

        writer = ProductWriteService(db)
        writer.set_custom_catalog_membership(product_id=included_id, catalog_slug=catalog.slug, is_assigned=True)
        db.commit()

        service = SiteQueryService(db)
        assigned_payload = service.catalog_products(
            limit=10,
            offset=0,
            query=marker,
            designer_slugs=[],
            gender_values=[],
            filter_slugs=[],
            custom_catalog_slug=catalog.slug,
            availability_mode=None,
            orderability_status=None,
            discounted_only=False,
        )
        assert {item.id for item in assigned_payload.items} == {included_id}
        assert assigned_payload.total == 1

        writer.set_custom_catalog_membership(product_id=included_id, catalog_slug=catalog.slug, is_assigned=False)
        writer.set_custom_catalog_membership(product_id=excluded_id, catalog_slug=catalog.slug, is_assigned=True)
        db.commit()

        reassigned_payload = service.catalog_products(
            limit=10,
            offset=0,
            query=marker,
            designer_slugs=[],
            gender_values=[],
            filter_slugs=[],
            custom_catalog_slug=catalog.slug,
            availability_mode=None,
            orderability_status=None,
            discounted_only=False,
        )
        assert {item.id for item in reassigned_payload.items} == {excluded_id}
        assert reassigned_payload.total == 1

        preview = AdminShowcasePreviewService(db)
        preview._taxonomy_state = {
            "filters": [],
            "custom_catalogs": [{
                "id": int(catalog.id),
                "slug": catalog.slug,
                "label": catalog.title,
                "description": catalog.description,
                "is_enabled": True,
                "manual_products": [],
            }],
            "categories": [{
                "behavior": "new",
                "attachments": [{"kind": "custom_catalog", "ref_id": int(catalog.id)}],
            }],
            "designer_directory": [],
        }
        navigation = preview.navigation()
        new_menu = next(section["menu"] for section in navigation["sections"] if section["key"] == "new")
        collection_item = next(
            item
            for block in new_menu["blocks"]
            for item in block["items"]
            if item["id"] == f"new-catalog-{catalog.id}"
        )
        assert collection_item["target"]["query"] == {
            "collection": catalog.slug,
            "ctx": "custom",
            "ctx_ref": catalog.slug,
        }
    finally:
        db.rollback()
        db.close()
