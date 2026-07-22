from __future__ import annotations

import base64
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.core.database import SessionLocal
from app.core.source_identity import normalize_base_url
from app.models import Designer, DesignerSourceName, ImageAsset, ProductListing, ShowcaseCarouselImage, ShowcaseSetting, Source, SourceSetting, Supplier
from app.services.catalog.media_asset_service import MediaAssetService
from app.services.catalog.source_registry_service import SourceRegistryService
from app.services.settings.settings_transfer_service import SettingsTransferService


def _image_entry(scope: str, file_name: str, content: bytes) -> dict[str, object]:
    return {
        "checksum_sha256": sha256(content).hexdigest(),
        "scope": scope,
        "file_name": file_name,
        "mime_type": "image/svg+xml",
        "byte_size": len(content),
        "width_px": None,
        "height_px": None,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _reset_showcase_media_state(db) -> None:
    db.query(ShowcaseCarouselImage).delete(synchronize_session=False)
    settings = db.query(ShowcaseSetting).order_by(ShowcaseSetting.id.asc()).first()
    if settings is not None:
        settings.desktop_hero_image_asset_id = None
        settings.mobile_hero_image_asset_id = None
    db.query(ImageAsset).filter(ImageAsset.storage_key.like("showcase/%")).delete(synchronize_session=False)
    db.flush()


def test_settings_transfer_roundtrip_covers_manual_source_designers_taxonomy_and_showcase_media() -> None:
    db = SessionLocal()
    _reset_showcase_media_state(db)
    service = SettingsTransferService(db)
    marker = uuid4().hex[:10]
    source_brand = f"Transfer Brand {marker}"
    designer_name = f"Transfer Designer {marker}"
    designer_slug = f"transfer-designer-{marker}"
    filter_slug = f"transfer-filter-{marker}"
    catalog_slug = f"transfer-catalog-{marker}"
    title_override = f"Витрина {marker}"
    default_weight_grams = 2300

    shared_logo_hero_bytes = f"<svg xmlns='http://www.w3.org/2000/svg'><text>{marker}-shared</text></svg>".encode("utf-8")
    manual_logo_bytes = shared_logo_hero_bytes
    hero_bytes = shared_logo_hero_bytes
    carousel_bytes = f"<svg xmlns='http://www.w3.org/2000/svg'><text>{marker}-carousel</text></svg>".encode("utf-8")
    manual_logo_entry = _image_entry("sources", f"{marker}-logo.svg", manual_logo_bytes)
    hero_entry = _image_entry("showcase", f"{marker}-hero.svg", hero_bytes)
    carousel_entry = _image_entry("showcase", f"{marker}-carousel.svg", carousel_bytes)

    original_payload = service.export_payload()
    restore_payload = original_payload.model_copy(deep=True)

    try:
        payload_data = original_payload.model_dump()
        payload_data["image_assets"] = [*payload_data["image_assets"], manual_logo_entry, hero_entry, carousel_entry]

        manual_source = next(item for item in payload_data["sources"] if item["key"] == SourceRegistryService.MANUAL_SOURCE_KEY)
        manual_source["logo_asset_checksum"] = str(manual_logo_entry["checksum_sha256"])
        manual_source["enabled"] = False

        payload_data["designers"] = [
            *payload_data["designers"],
            {
                "name": designer_name,
                "slug": designer_slug,
                "description": f"Описание {marker}",
                "origin_kind": "manual",
                "is_admin_touched": True,
                "is_enabled": True,
            },
        ]
        payload_data["designer_source_names"] = [
            *payload_data["designer_source_names"],
            {
                "source_name": source_brand,
                "designer_name": designer_name,
                "is_enabled": False,
                "is_admin_touched": True,
            },
        ]

        payload_data["taxonomy"]["filters"] = [
            {
                "slug": filter_slug,
                "title": f"Filter {marker}",
                "display_title": None,
                "mobile_pair_slug": None,
                "node_kind": "multifilter",
                "is_enabled": True,
                "restrict_by_gender": True,
                "local_category_keywords": [],
                "title_keywords": [],
                "children": [
                    {
                        "slug": f"{filter_slug}-child",
                        "title": f"Filter Child {marker}",
                        "display_title": None,
                        "mobile_pair_slug": None,
                        "default_weight_grams": default_weight_grams,
                        "node_kind": "filter",
                        "is_enabled": True,
                        "restrict_by_gender": False,
                        "local_category_keywords": ["child"],
                        "title_keywords": ["child"],
                        "children": [],
                    }
                ],
            }
        ]
        payload_data["weight_rules"] = [
            *payload_data["weight_rules"],
            {
                "weight_grams": default_weight_grams,
                "keywords": ["transfer-bag"],
            },
        ]
        payload_data["taxonomy"]["custom_catalogs"] = [
            {
                "slug": catalog_slug,
                "title": f"Catalog {marker}",
                "description": f"Catalog description {marker}",
                "is_enabled": True,
            }
        ]
        payload_data["taxonomy"]["showcase_categories"] = [
            {
                "code": item["code"],
                "title": (title_override if item["code"] == "new" else item["title"]),
                "attachments": (
                    [
                        {
                            "kind": "filter",
                            "filter_slug": filter_slug,
                            "custom_catalog_slug": None,
                            "hidden_filter_slugs": [],
                        },
                        {
                            "kind": "custom_catalog",
                            "filter_slug": None,
                            "custom_catalog_slug": catalog_slug,
                            "hidden_filter_slugs": [],
                        },
                    ]
                    if item["code"] == "new"
                    else []
                ),
            }
            for item in payload_data["taxonomy"]["showcase_categories"]
        ]

        payload_data["showcase_media"] = {
            "desktop_hero_asset_checksum": str(hero_entry["checksum_sha256"]),
            "mobile_hero_asset_checksum": None,
            "desktop_carousel": [
                {
                    "asset_checksum": str(carousel_entry["checksum_sha256"]),
                    "viewport": "desktop",
                    "position": 1,
                }
            ],
            "mobile_carousel": [],
        }

        payload = original_payload.__class__.model_validate(payload_data)

        import_result = SettingsTransferService(db).import_payload(payload)
        assert import_result.ok is True

        exported = SettingsTransferService(db).export_payload()
        exported_manual = next(item for item in exported.sources if item.key == SourceRegistryService.MANUAL_SOURCE_KEY)
        assert exported_manual.logo_asset_checksum == manual_logo_entry["checksum_sha256"]
        assert exported_manual.enabled is False

        exported_designer = next(item for item in exported.designers if item.slug == designer_slug)
        assert exported_designer.name == designer_name
        assert exported_designer.description == f"Описание {marker}"

        exported_mapping = next(item for item in exported.designer_source_names if item.source_name == source_brand)
        assert exported_mapping.designer_name == designer_name
        assert exported_mapping.is_enabled is False
        assert exported_mapping.is_admin_touched is True

        exported_showcase_new = next(item for item in exported.taxonomy.showcase_categories if item.code == "new")
        assert exported_showcase_new.title == title_override
        assert not any(item.kind == "filter" for item in exported_showcase_new.attachments)
        assert any(item.custom_catalog_slug == catalog_slug for item in exported_showcase_new.attachments if item.kind == "custom_catalog")

        exported_filter = exported.taxonomy.filters[0].children[0]
        assert exported_filter.default_weight_grams == default_weight_grams
        assert exported_filter.restrict_by_gender is False

        assert exported.showcase_media.desktop_hero_asset_checksum == hero_entry["checksum_sha256"]
        assert exported.showcase_media.mobile_hero_asset_checksum is None
        assert [item.asset_checksum for item in exported.showcase_media.desktop_carousel] == [carousel_entry["checksum_sha256"]]
        assert [item.viewport for item in exported.showcase_media.desktop_carousel] == ["desktop"]
        assert exported.showcase_media.mobile_carousel == []
        exported_asset_keys = {(item.scope, item.checksum_sha256) for item in exported.image_assets}
        assert ("sources", str(manual_logo_entry["checksum_sha256"])) in exported_asset_keys
        assert ("showcase", str(hero_entry["checksum_sha256"])) in exported_asset_keys
        assert ("showcase", str(carousel_entry["checksum_sha256"])) in exported_asset_keys
    finally:
        SettingsTransferService(db).import_payload(restore_payload)

        for row in db.query(DesignerSourceName).filter(DesignerSourceName.source_name == source_brand).all():
            db.delete(row)
        for row in db.query(Designer).filter(Designer.slug == designer_slug).all():
            db.delete(row)
        db.flush()

        media_service = MediaAssetService(db)
        for checksum in {
            str(manual_logo_entry["checksum_sha256"]),
            str(hero_entry["checksum_sha256"]),
            str(carousel_entry["checksum_sha256"]),
        }:
            for asset in db.query(ImageAsset).filter(ImageAsset.checksum_sha256 == checksum).all():
                file_path = media_service.resolve_file_path(asset)
                if file_path.exists():
                    file_path.unlink()
                db.delete(asset)
        db.commit()
        db.close()


def test_settings_transfer_roundtrip_restores_pricing_ui_supplier_source_and_weight_rules() -> None:
    db = SessionLocal()
    _reset_showcase_media_state(db)
    service = SettingsTransferService(db)
    original_payload = service.export_payload()
    restore_payload = original_payload.model_copy(deep=True)

    try:
        payload_data = original_payload.model_dump()

        payload_data["pricing_settings"].update(
            {
                "markup_multiplier": 4.25,
                "weight_tolerance": 1.7,
                "customs_threshold_eur": 333.0,
                "customs_duty_rate": 0.21,
                "eur_to_usd_rate": 1.18,
                "gbp_to_usd_rate": 1.4,
                "jpy_to_usd_rate": 0.0065,
                "eur_to_rub_rate": 123.45,
                "usd_to_rub_rate": 111.11,
                "usdt_to_rub_rate": 109.99,
                "usdt_extra_rub": 17.0,
                "final_rounding_mode": "ceil_100",
                "payment_fee_rate": 0.09,
                "customs_processing_rate": 0.13,
                "customs_fixed_rub": 4444.0,
                "tax_rate": 0.08,
                "svc_rules": [
                    {"min_rub": 0.0, "max_rub": 15000.0, "mode": "fixed_rub", "value": 2500.0},
                    {"min_rub": 15000.0, "max_rub": None, "mode": "percent", "value": 0.18},
                ],
            }
        )
        payload_data["admin_ui_settings"]["auto_sync_period_minutes"] = 720

        primary_supplier = payload_data["suppliers"][0]
        secondary_supplier = payload_data["suppliers"][1]
        primary_supplier["name"] = "Roundtrip Primary Supplier"
        primary_supplier["rate_currency"] = "USD"
        primary_supplier["is_enabled"] = False
        primary_supplier["rates"] = [
            {"min_kg": 0.0, "max_kg": 0.5, "rub": 1234.0},
            {"min_kg": 0.5, "max_kg": None, "rub": 4321.0},
        ]
        secondary_supplier["name"] = "Roundtrip Alternate Supplier"
        secondary_supplier["provider_kind"] = "alternate"
        secondary_supplier["parent_supplier_key"] = primary_supplier["key"]
        secondary_supplier["rate_currency"] = "EUR"
        secondary_supplier["is_enabled"] = True
        secondary_supplier["rates"] = [
            {"min_kg": 0.0, "max_kg": 1.0, "rub": 2222.0},
        ]

        source_entry = next(
            item
            for item in payload_data["sources"]
            if item["key"] != SourceRegistryService.MANUAL_SOURCE_KEY
        )
        source_entry["name"] = "Roundtrip Source Name"
        source_entry["url"] = "https://roundtrip-source.example/"
        source_entry["adapter_key"] = "roundtrip-adapter"
        source_entry["parser_config"] = {"mode": "roundtrip", "flag": True}
        source_entry["sort_priority"] = 7
        source_entry["enabled"] = False
        source_entry["sync_enabled"] = False
        source_entry["dedup_enabled"] = False
        source_entry["hide_auto_added_products"] = True
        source_entry["description_mode"] = "hidden"
        source_entry["show_images"] = False
        source_entry["clean_public_titles"] = True
        source_entry["supplier_key"] = secondary_supplier["key"]
        source_entry["promo_factor"] = 1.35
        source_entry["promo_only_no_discount"] = True
        source_entry["buyout_surcharge_value"] = 77.0
        source_entry["buyout_surcharge_currency"] = "USD"

        payload_data["weight_rules"] = [
            {"weight_grams": 321, "keywords": ["roundtrip-light", "roundtrip-slim"]},
            {"weight_grams": 987, "keywords": ["roundtrip-heavy"]},
        ]

        payload = original_payload.__class__.model_validate(payload_data)
        import_result = SettingsTransferService(db).import_payload(payload)
        assert import_result.ok is True

        exported = SettingsTransferService(db).export_payload()
        assert exported.pricing_settings.model_dump() == payload.pricing_settings.model_dump()
        assert exported.admin_ui_settings.auto_sync_period_minutes == 720

        exported_primary_supplier = next(item for item in exported.suppliers if item.key == primary_supplier["key"])
        assert exported_primary_supplier.name == "Roundtrip Primary Supplier"
        assert exported_primary_supplier.provider_kind == "main"
        assert exported_primary_supplier.parent_supplier_key is None
        assert exported_primary_supplier.rate_currency == "USD"
        assert exported_primary_supplier.is_enabled is False
        assert [rate.model_dump() for rate in exported_primary_supplier.rates] == primary_supplier["rates"]

        exported_secondary_supplier = next(item for item in exported.suppliers if item.key == secondary_supplier["key"])
        assert exported_secondary_supplier.name == "Roundtrip Alternate Supplier"
        assert exported_secondary_supplier.provider_kind == "alternate"
        assert exported_secondary_supplier.parent_supplier_key == primary_supplier["key"]
        assert exported_secondary_supplier.rate_currency == "EUR"
        assert exported_secondary_supplier.is_enabled is True
        assert [rate.model_dump() for rate in exported_secondary_supplier.rates] == secondary_supplier["rates"]

        exported_source = next(item for item in exported.sources if item.key == source_entry["key"])
        assert exported_source.name == "Roundtrip Source Name"
        assert exported_source.url == "https://roundtrip-source.example/"
        assert exported_source.adapter_key == "roundtrip-adapter"
        assert exported_source.parser_config == {"mode": "roundtrip", "flag": True}
        assert exported_source.sort_priority == 7
        assert exported_source.enabled is False
        assert exported_source.sync_enabled is False
        assert exported_source.dedup_enabled is False
        assert exported_source.hide_auto_added_products is True
        assert exported_source.description_mode == "hidden"
        assert exported_source.show_images is False
        assert exported_source.clean_public_titles is True
        assert exported_source.supplier_key == secondary_supplier["key"]
        assert exported_source.promo_factor == 1.35
        assert exported_source.promo_only_no_discount is True
        assert exported_source.buyout_surcharge_value == 77.0
        assert exported_source.buyout_surcharge_currency == "USD"

        assert [item.model_dump() for item in exported.weight_rules] == payload_data["weight_rules"]
    finally:
        SettingsTransferService(db).import_payload(restore_payload)
        db.commit()
        db.close()


def test_settings_transfer_roundtrip_restores_site_content_without_photos() -> None:
    db = SessionLocal()
    _reset_showcase_media_state(db)
    service = SettingsTransferService(db)
    original_payload = service.export_payload()
    restore_payload = original_payload.model_copy(deep=True)
    marker = uuid4().hex[:10]

    try:
        payload_data = original_payload.model_dump()
        payload_data["site_content"] = {
            "about": {
                "text": f"Обо мне без фото {marker}",
                "photo_asset_checksums": [],
            },
            "questions": [
                {
                    "question": f"Вопрос A {marker}",
                    "answer": f"Ответ A {marker}",
                    "is_enabled": True,
                    "is_expanded_by_default": False,
                    "position": 1,
                },
                {
                    "question": f"Вопрос B {marker}",
                    "answer": f"Ответ B {marker}",
                    "is_enabled": False,
                    "is_expanded_by_default": True,
                    "position": 2,
                },
            ],
            "notifications": [
                {
                    "title": f"Уведомление A {marker}",
                    "description": f"Описание A {marker}",
                    "button_text": "Открыть",
                    "button_url": "/catalog",
                    "image_asset_checksum": None,
                    "version": 3,
                    "position": 1,
                },
                {
                    "title": f"Уведомление B {marker}",
                    "description": f"Описание B {marker}",
                    "button_text": "Подробнее",
                    "button_url": "https://example.com/content",
                    "image_asset_checksum": None,
                    "version": 1,
                    "position": 2,
                },
            ],
        }

        payload = original_payload.__class__.model_validate(payload_data)
        import_result = SettingsTransferService(db).import_payload(payload)
        assert import_result.ok is True
        assert import_result.imported_counts["site_about_photos_linked"] == 0
        assert import_result.imported_counts["site_notifications_replaced"] == 2
        assert import_result.imported_counts["site_questions_replaced"] == 2

        exported = SettingsTransferService(db).export_payload()
        assert exported.site_content.about.text == f"Обо мне без фото {marker}"
        assert exported.site_content.about.photo_asset_checksums == []
        assert [item.model_dump() for item in exported.site_content.questions] == payload_data["site_content"]["questions"]
        assert [item.model_dump() for item in exported.site_content.notifications] == payload_data["site_content"]["notifications"]
    finally:
        SettingsTransferService(db).import_payload(restore_payload)
        db.commit()
        db.close()


def test_settings_transfer_import_of_exported_payload_is_idempotent_for_pricing_counts() -> None:
    db = SessionLocal()
    _reset_showcase_media_state(db)
    service = SettingsTransferService(db)
    payload = service.export_payload()

    try:
        result = SettingsTransferService(db).import_payload(payload)
        assert result.imported_counts["pricing_settings_updated"] == 0
        assert result.imported_counts["admin_ui_settings_updated"] == 0

        re_exported = SettingsTransferService(db).export_payload().model_dump()
        original = payload.model_dump()
        re_exported.pop("exported_at", None)
        original.pop("exported_at", None)
        assert re_exported == original
    finally:
        db.rollback()
        db.close()


def test_settings_transfer_import_rejects_missing_logo_asset_checksum_reference() -> None:
    db = SessionLocal()
    _reset_showcase_media_state(db)
    service = SettingsTransferService(db)
    payload = service.export_payload()
    payload_data = payload.model_dump()
    manual_source = next(item for item in payload_data["sources"] if item["key"] == SourceRegistryService.MANUAL_SOURCE_KEY)
    manual_source["logo_asset_checksum"] = "f" * 64

    try:
        SettingsTransferService(db).import_payload(payload.__class__.model_validate(payload_data))
        raise AssertionError("Expected import to fail on missing source logo image asset")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "Логотип источника" in str(exc.detail)
    finally:
        db.rollback()
        db.close()


def test_settings_transfer_failed_import_cleans_up_new_image_files() -> None:
    db = SessionLocal()
    _reset_showcase_media_state(db)
    service = SettingsTransferService(db)
    payload = service.export_payload()
    payload_data = payload.model_dump()

    content = b"<svg xmlns='http://www.w3.org/2000/svg'><text>cleanup-check</text></svg>"
    checksum = sha256(content).hexdigest()
    payload_data["image_assets"].append(_image_entry("showcase", "cleanup-check.svg", content))
    payload_data["showcase_media"]["desktop_hero_asset_checksum"] = checksum

    broken_source = next(item for item in payload_data["sources"] if item["key"] != SourceRegistryService.MANUAL_SOURCE_KEY)
    broken_source["supplier_key"] = "missing-supplier-key"

    uploads_root = Path(MediaAssetService._ROOT_DIR)
    before_files = sorted(str(path.relative_to(uploads_root)) for path in uploads_root.rglob("*") if path.is_file())

    try:
        SettingsTransferService(db).import_payload(payload.__class__.model_validate(payload_data))
        raise AssertionError("Expected import to fail on missing supplier reference")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "не найден назначенный тариф" in str(exc.detail)
        after_files = sorted(str(path.relative_to(uploads_root)) for path in uploads_root.rglob("*") if path.is_file())
        assert after_files == before_files
    finally:
        db.rollback()
        db.close()


def test_settings_transfer_import_rejects_checksum_mismatch_image_asset() -> None:
    db = SessionLocal()
    _reset_showcase_media_state(db)
    service = SettingsTransferService(db)
    payload = service.export_payload()
    payload_data = payload.model_dump()

    content = b"<svg xmlns='http://www.w3.org/2000/svg'><text>checksum-mismatch</text></svg>"
    payload_data["image_assets"].append(
        {
            **_image_entry("showcase", "checksum-mismatch.svg", content),
            "checksum_sha256": "a" * 64,
        }
    )
    payload_data["showcase_media"]["desktop_hero_asset_checksum"] = "a" * 64

    try:
        SettingsTransferService(db).import_payload(payload.__class__.model_validate(payload_data))
        raise AssertionError("Expected import to fail on checksum mismatch")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "Контрольная сумма медиафайла не совпадает" in str(exc.detail)
    finally:
        db.rollback()
        db.close()


def test_settings_transfer_import_prunes_unreferenced_source_supplier_and_designer_missing_from_file() -> None:
    db = SessionLocal()
    _reset_showcase_media_state(db)
    marker = uuid4().hex[:10]
    extra_supplier_key = f"extra-supplier-{marker}"
    extra_source_key = f"extra-source-{marker}.example"
    extra_source_url = f"https://{extra_source_key}/"
    extra_designer_slug = f"extra-designer-{marker}"

    supplier = Supplier(
        key=extra_supplier_key,
        name=f"Extra Supplier {marker}",
        provider_kind="main",
        rate_currency="RUB",
        is_enabled=True,
    )
    db.add(supplier)
    db.flush()

    source = Source(
        key=extra_source_key,
        name=f"Extra Source {marker}",
        base_url=extra_source_url,
        base_url_normalized=normalize_base_url(extra_source_url),
        adapter_key=f"adapter-{marker}",
        parser_config={},
    )
    db.add(source)
    db.flush()
    db.add(SourceSetting(source_id=int(source.id), supplier_id=int(supplier.id)))

    designer = Designer(
        name=f"Extra Designer {marker}",
        slug=extra_designer_slug,
        origin_kind="manual",
        is_admin_touched=True,
        is_enabled=True,
    )
    db.add(designer)
    db.flush()

    service = SettingsTransferService(db)
    original_payload = service.export_payload()
    restore_payload = original_payload.model_copy(deep=True)

    try:
        payload_data = original_payload.model_dump()
        payload_data["sources"] = [item for item in payload_data["sources"] if item["key"] != extra_source_key]
        payload_data["suppliers"] = [item for item in payload_data["suppliers"] if item["key"] != extra_supplier_key]
        payload_data["designers"] = [item for item in payload_data["designers"] if item["slug"] != extra_designer_slug]

        payload = original_payload.__class__.model_validate(payload_data)
        import_result = SettingsTransferService(db).import_payload(payload)
        assert import_result.ok is True
        assert import_result.imported_counts["sources_deleted"] >= 1
        assert import_result.imported_counts["suppliers_deleted"] >= 1
        assert import_result.imported_counts["designers_deleted"] >= 1

        assert db.query(Source).filter(Source.key == extra_source_key).one_or_none() is None
        assert db.query(Supplier).filter(Supplier.key == extra_supplier_key).one_or_none() is None
        assert db.query(Designer).filter(Designer.slug == extra_designer_slug).one_or_none() is None
    finally:
        SettingsTransferService(db).import_payload(restore_payload)
        db.commit()
        db.close()


def test_settings_transfer_import_rejects_missing_in_use_source() -> None:
    db = SessionLocal()
    _reset_showcase_media_state(db)
    marker = uuid4().hex[:10]
    source_key = f"in-use-source-{marker}.example"
    source_url = f"https://{source_key}/"

    source = Source(
        key=source_key,
        name=f"In Use Source {marker}",
        base_url=source_url,
        base_url_normalized=normalize_base_url(source_url),
        adapter_key=f"adapter-{marker}",
        parser_config={},
    )
    db.add(source)
    db.flush()
    db.add(SourceSetting(source_id=int(source.id), supplier_id=None))
    db.flush()
    db.add(
        ProductListing(
            source_id=int(source.id),
            external_id=f"external-{marker}",
            url=f"{source_url}product",
            url_normalized=f"{source_url}product",
            host_normalized=source_key,
            source_title=f"Product {marker}",
            ingest_mode="sync",
        )
    )
    db.flush()

    service = SettingsTransferService(db)
    original_payload = service.export_payload()

    payload_data = original_payload.model_dump()
    payload_data["sources"] = [item for item in payload_data["sources"] if item["key"] != source_key]
    payload = original_payload.__class__.model_validate(payload_data)

    try:
        SettingsTransferService(db).import_payload(payload)
        raise AssertionError("Expected import to fail when file misses an in-use source")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert source.name in str(exc.detail)
    finally:
        db.rollback()
        db.close()
