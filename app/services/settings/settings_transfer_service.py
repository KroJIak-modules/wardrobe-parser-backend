"""Export/import of admin settings (without product data)."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    AdminUiSettings,
    Designer,
    DesignerSourceName,
    ImageAsset,
    PricingSetting,
    Product,
    ProductListing,
    SiteAboutPhoto,
    SiteAboutSetting,
    SiteAccessSetting,
    SiteNotificationSetting,
    SiteQuestionItem,
    Source,
    SourceSetting,
    ShowcaseCarouselImage,
    ShowcaseSetting,
    Supplier,
    SupplierShippingRate,
    WeightRule,
    WeightRuleKeyword,
)
from app.repositories import (
    CatalogPricingSettingsRepository,
    CatalogSourceRepository,
    CatalogSupplierRepository,
    CatalogWeightRuleRepository,
)
from app.services.catalog.designer_support import slugify_designer_name
from app.services.catalog.designer_catalog_sync_service import DesignerCatalogSyncService
from app.services.catalog.catalog_defaults_service import CatalogDefaultsService
from app.services.catalog.media_asset_service import MediaAssetService
from app.services.catalog.showcase_service import ShowcaseService
from app.services.catalog.source_registry_service import SourceRegistryService
from app.services.catalog.taxonomy_service import TaxonomyService
from app.core.source_identity import normalize_base_url
from app.schemas.taxonomy import (
    TaxonomyCustomCatalog,
    TaxonomyFilterNode,
    TaxonomyShowcaseAttachment,
    TaxonomyShowcaseCategory,
    TaxonomyState,
)
from app.services.settings.pricing_service import PricingSettingsService
from app.services.settings.default_admin_settings import DefaultAdminSettingsLoader
from app.schemas.admin_settings import (
    SettingsTransferAdminUiSettings,
    SettingsTransferDesignerEntry,
    SettingsTransferDesignerSourceNameEntry,
    SettingsTransferImageAssetEntry,
    SettingsTransferPayload,
    SettingsTransferPricingSettings,
    SettingsTransferResponse,
    SettingsTransferSourceEntry,
    SettingsTransferSupplierEntry,
    SettingsTransferTaxonomyCustomCatalog,
    SettingsTransferTaxonomyFilterNode,
    SettingsTransferTaxonomyShowcaseAttachment,
    SettingsTransferTaxonomyShowcaseCategory,
    SettingsTransferTaxonomyState,
    SettingsTransferShowcaseCarouselEntry,
    SettingsTransferShowcaseMedia,
    SettingsTransferSiteAccess,
    SettingsTransferSiteAbout,
    SettingsTransferSiteContent,
    SettingsTransferSiteNotification,
    SettingsTransferSiteQuestionItem,
    SettingsTransferWeightRuleEntry,
)
from app.schemas.showcase_media import ShowcaseStateUpdateRequest
from app.services.catalog.site_content_service import SiteContentService
from app.services.auth.passwords import hash_password

_SCHEMA_VERSION = 9
_PROJECT_NAME = "wardrobe-parser-platform"

_PRICING_EXPORT_FIELDS = [
    "markup_multiplier",
    "weight_tolerance",
    "customs_threshold_eur",
    "customs_duty_rate",
    "eur_to_usd_rate",
    "gbp_to_usd_rate",
    "jpy_to_usd_rate",
    "eur_to_rub_rate",
    "usd_to_rub_rate",
    "usdt_to_rub_rate",
    "usdt_extra_rub",
    "final_rounding_mode",
    "payment_fee_rate",
    "customs_processing_rate",
    "customs_fixed_rub",
    "tax_rate",
    "svc_rules",
]

_PRICING_IMPORT_FIELDS = set(_PRICING_EXPORT_FIELDS)


def _normalize_currency(raw: str | None, *, default: str = "RUB") -> str:
    value = (raw or default).strip().upper()
    if value not in {"RUB", "USD", "EUR", "GBP", "JPY"}:
        return default
    return value


def _normalize_supplier_key(raw_key: str, fallback_name: str, index: int) -> str:
    source = (raw_key or "").strip().lower()
    if not source:
        source = fallback_name.strip().lower()
    source = re.sub(r"[^a-z0-9]+", "-", source).strip("-")
    if not source:
        source = f"supplier-{index}"
    return source[:64]


def _slugify_name(raw: str) -> str:
    return slugify_designer_name(raw)


def _numeric_values_equal(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError, TypeError):
        return False


class SettingsTransferService:
    """Application service for settings export/import."""

    def __init__(self, db: Session):
        self.db = db
        self.pricing_repo = CatalogPricingSettingsRepository(db)
        self.supplier_repo = CatalogSupplierRepository(db)
        self.source_repo = CatalogSourceRepository(db)
        self.weight_rule_repo = CatalogWeightRuleRepository(db)
        self.media_assets = MediaAssetService(db)
        self.taxonomy = TaxonomyService(db)

    @staticmethod
    def _normalize_designer_key(raw: str | None) -> str:
        return " ".join(str(raw or "").strip().split()).lower()

    @staticmethod
    def _asset_scope_for_export(asset: ImageAsset, *, fallback: str) -> str:
        explicit_scope = str(getattr(asset, "scope", "") or "").strip()
        if explicit_scope:
            return explicit_scope
        raw = str(getattr(asset, "storage_key", "") or "").strip()
        if not raw or "/" not in raw:
            return fallback
        prefix = raw.split("/", 1)[0].strip()
        return prefix or fallback

    @staticmethod
    def _asset_file_name(storage_key: str | None, *, fallback: str) -> str:
        raw = str(storage_key or "").strip()
        if not raw or "/" not in raw:
            return fallback
        file_name = raw.rsplit("/", 1)[-1].strip()
        return file_name or fallback

    def _export_image_assets(self, assets: list[tuple[ImageAsset, str]]) -> list[SettingsTransferImageAssetEntry]:
        seen_asset_keys: set[tuple[str, str]] = set()
        entries: list[SettingsTransferImageAssetEntry] = []
        for asset, default_scope in assets:
            checksum = str(getattr(asset, "checksum_sha256", "") or "").strip()
            scope = self._asset_scope_for_export(asset, fallback=default_scope)
            asset_key = (scope, checksum)
            if not checksum or asset_key in seen_asset_keys:
                continue
            file_path = self.media_assets.resolve_file_path(asset)
            if not file_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Медиафайл не найден: {asset.storage_key}",
                )
            content = file_path.read_bytes()
            seen_asset_keys.add(asset_key)
            entries.append(
                SettingsTransferImageAssetEntry(
                    checksum_sha256=checksum,
                    scope=scope,
                    file_name=self._asset_file_name(getattr(asset, "storage_key", None), fallback=f"{checksum}.bin"),
                    mime_type=str(getattr(asset, "mime_type", "") or "application/octet-stream"),
                    byte_size=int(getattr(asset, "byte_size", 0) or 0),
                    width_px=(int(asset.width_px) if getattr(asset, "width_px", None) is not None else None),
                    height_px=(int(asset.height_px) if getattr(asset, "height_px", None) is not None else None),
                    content_base64=base64.b64encode(content).decode("ascii"),
                )
            )
        return entries

    @classmethod
    def _serialize_taxonomy_filters(cls, nodes: list[TaxonomyFilterNode]) -> list[SettingsTransferTaxonomyFilterNode]:
        return [
            SettingsTransferTaxonomyFilterNode(
                slug=str(node.slug or "").strip(),
                title=str(node.title),
                display_title=(str(node.display_title) if node.display_title else None),
                mobile_pair_slug=(str(node.mobile_pair_slug) if node.mobile_pair_slug else None),
                default_weight_grams=None,
                node_kind=str(node.node_kind),
                is_enabled=bool(node.is_enabled),
                local_category_keywords=[str(item) for item in node.local_category_keywords],
                title_keywords=[str(item) for item in node.title_keywords],
                children=cls._serialize_taxonomy_filters(node.children),
            )
            for node in nodes
            if str(node.slug or "").strip()
        ]

    def _export_taxonomy(self) -> SettingsTransferTaxonomyState:
        state = self.taxonomy.get_state()
        weight_rule_by_id = {
            int(rule.id): int(rule.weight_grams)
            for rule in self.weight_rule_repo.list_active()
            if rule.id is not None and rule.weight_grams is not None
        }

        def inject_weight_grams(nodes: list[SettingsTransferTaxonomyFilterNode], source_nodes: list[TaxonomyFilterNode]) -> None:
            source_by_slug = {
                str(node.slug or "").strip(): node
                for node in source_nodes
                if str(node.slug or "").strip()
            }
            for node in nodes:
                source = source_by_slug.get(str(node.slug or "").strip())
                rule_id = int(source.default_weight_rule_id) if source is not None and source.default_weight_rule_id is not None else None
                node.default_weight_grams = weight_rule_by_id.get(rule_id) if rule_id is not None else None
                inject_weight_grams(node.children, source.children if source is not None else [])

        serialized_filters = self._serialize_taxonomy_filters(state.filters)
        inject_weight_grams(serialized_filters, state.filters)
        return SettingsTransferTaxonomyState(
            filters=serialized_filters,
            custom_catalogs=[
                SettingsTransferTaxonomyCustomCatalog(
                    slug=str(catalog.slug or "").strip(),
                    title=str(catalog.title),
                    description=(str(catalog.description) if catalog.description else None),
                    is_enabled=bool(catalog.is_enabled),
                )
                for catalog in state.custom_catalogs
                if str(catalog.slug or "").strip()
            ],
            showcase_categories=[
                SettingsTransferTaxonomyShowcaseCategory(
                    code=str(category.code),
                    title=str(category.title),
                    attachments=[
                        SettingsTransferTaxonomyShowcaseAttachment(
                            kind=str(attachment.kind),
                            filter_slug=(str(attachment.filter_slug) if attachment.filter_slug else None),
                            custom_catalog_slug=(str(attachment.custom_catalog_slug) if attachment.custom_catalog_slug else None),
                            hidden_filter_slugs=[str(item) for item in attachment.hidden_filter_slugs],
                        )
                        for attachment in category.attachments
                    ],
                )
                for category in state.showcase_categories
            ],
        )

    def _export_site_content(self) -> SettingsTransferSiteContent:
        about = self.db.query(SiteAboutSetting).order_by(SiteAboutSetting.id.asc()).first()
        access = self.db.query(SiteAccessSetting).order_by(SiteAccessSetting.id.asc()).first()
        notifications = (
            self.db.query(SiteNotificationSetting)
            .filter(SiteNotificationSetting.deleted_at.is_(None))
            .order_by(SiteNotificationSetting.created_at.asc(), SiteNotificationSetting.id.asc())
            .all()
        )
        about_rows = (
            self.db.query(SiteAboutPhoto)
            .join(ImageAsset, ImageAsset.id == SiteAboutPhoto.image_asset_id)
            .order_by(SiteAboutPhoto.position.asc(), SiteAboutPhoto.id.asc())
            .all()
        )
        return SettingsTransferSiteContent(
            access=SettingsTransferSiteAccess(
                enabled=bool(getattr(access, "enabled", False)),
                title=str(getattr(access, "title", "") or ""),
                description=str(getattr(access, "description", "") or ""),
                password=str(getattr(access, "password_value", "") or ""),
            ),
            about=SettingsTransferSiteAbout(
                text=str(getattr(about, "body_text", "") or ""),
                photo_asset_checksums=[
                    str(row.image_asset.checksum_sha256)
                    for row in about_rows
                    if getattr(row, "image_asset", None) is not None
                    and str(getattr(row.image_asset, "checksum_sha256", "") or "").strip()
                ],
            ),
            notifications=[
                SettingsTransferSiteNotification(
                    title=str(getattr(notification, "title", "") or ""),
                    description=str(getattr(notification, "description", "") or ""),
                    button_text=str(getattr(notification, "button_text", "") or ""),
                    button_url=str(getattr(notification, "button_url", "") or ""),
                    image_asset_checksum=(
                        str(notification.image_asset.checksum_sha256)
                        if getattr(notification, "image_asset", None) is not None
                        and str(getattr(notification.image_asset, "checksum_sha256", "") or "").strip()
                        else None
                    ),
                    version=int(getattr(notification, "version", 1) or 1),
                    position=index,
                )
                for index, notification in enumerate(notifications, start=1)
            ],
            questions=[
                SettingsTransferSiteQuestionItem(
                    question=str(item.question or ""),
                    answer=str(item.answer or ""),
                    is_enabled=bool(item.is_enabled),
                    is_expanded_by_default=bool(item.is_expanded_by_default),
                    position=int(item.position),
                )
                for item in (
                    self.db.query(SiteQuestionItem)
                    .order_by(SiteQuestionItem.position.asc(), SiteQuestionItem.id.asc())
                    .all()
                )
            ],
        )

    @staticmethod
    def _import_taxonomy_filters(
        nodes: list[SettingsTransferTaxonomyFilterNode],
        *,
        weight_rule_id_by_grams: dict[int, int],
    ) -> list[TaxonomyFilterNode]:
        return [
            TaxonomyFilterNode(
                slug=str(node.slug).strip(),
                title=node.title,
                display_title=node.display_title,
                mobile_pair_slug=node.mobile_pair_slug,
                default_weight_rule_id=weight_rule_id_by_grams.get(int(node.default_weight_grams)) if node.default_weight_grams is not None else None,
                node_kind=node.node_kind,
                is_enabled=node.is_enabled,
                local_category_keywords=[str(item) for item in node.local_category_keywords],
                title_keywords=[str(item) for item in node.title_keywords],
                manual_product_ids=[],
                children=SettingsTransferService._import_taxonomy_filters(
                    node.children,
                    weight_rule_id_by_grams=weight_rule_id_by_grams,
                ),
            )
            for node in nodes
        ]

    def export_payload(self) -> SettingsTransferPayload:
        pricing_row = self.pricing_repo.get_singleton()
        ui_row = self.db.query(AdminUiSettings).filter(AdminUiSettings.id == 1).one_or_none()
        if pricing_row is None or ui_row is None:
            pricing_service = PricingSettingsService(self.db)
            pricing_service.reset_to_seed()
            self.db.flush()
            pricing_row = self.pricing_repo.get_singleton()
            ui_row = self.db.query(AdminUiSettings).filter(AdminUiSettings.id == 1).one_or_none()
        if pricing_row is None or ui_row is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось подготовить настройки к экспорту")
        suppliers = self.supplier_repo.list_all_with_rates()
        sources = SourceRegistryService(self.db).list_all()
        weight_rules = self.weight_rule_repo.list_active()
        showcase_media_settings = (
            self.db.query(ShowcaseCarouselImage)
            .order_by(ShowcaseCarouselImage.viewport.asc(), ShowcaseCarouselImage.position.asc(), ShowcaseCarouselImage.id.asc())
            .all()
        )
        showcase_setting = self.db.query(ShowcaseSetting).order_by(ShowcaseSetting.id.asc()).first()

        supplier_by_id = {int(supplier.id): supplier for supplier in suppliers}
        designer_rows = (
            self.db.query(Designer)
            .order_by(Designer.name.asc(), Designer.id.asc())
            .all()
        )
        mapping_rows = (
            self.db.query(DesignerSourceName)
            .order_by(DesignerSourceName.source_name.asc(), DesignerSourceName.id.asc())
            .all()
        )

        pricing = SettingsTransferPricingSettings(
            **{
                field: getattr(pricing_row, field)
                for field in _PRICING_EXPORT_FIELDS
            }
        )
        admin_ui = SettingsTransferAdminUiSettings(
            auto_sync_period_minutes=int(ui_row.auto_sync_period_minutes),
        )

        supplier_entries = [
            SettingsTransferSupplierEntry(
                key=str(supplier.key),
                name=str(supplier.name),
                provider_kind=str(getattr(supplier, "provider_kind", "main") or "main"),
                parent_supplier_key=(
                    str(supplier_by_id[int(supplier.parent_supplier_id)].key)
                    if getattr(supplier, "parent_supplier_id", None) is not None
                    and int(supplier.parent_supplier_id) in supplier_by_id
                    else None
                ),
                rate_currency=str(supplier.rate_currency),
                is_enabled=bool(getattr(supplier, "is_enabled", True)),
                rates=[
                    {
                        "min_kg": float(rate.min_weight_kg),
                        "max_kg": (float(rate.max_weight_kg) if rate.max_weight_kg is not None else None),
                        "rub": float(rate.price_rub),
                    }
                    for rate in sorted(
                        supplier.shipping_rates,
                        key=lambda item: (
                            float(item.min_weight_kg or 0.0),
                            float(item.max_weight_kg) if item.max_weight_kg is not None else float("inf"),
                        ),
                    )
                ],
            )
            for supplier in suppliers
        ]

        source_entries: list[SettingsTransferSourceEntry] = []
        export_assets: list[tuple[ImageAsset, str]] = []
        for source in sources:
            setting = self.source_repo.ensure_setting(source)
            if getattr(source, "logo_image_asset", None) is not None:
                export_assets.append((source.logo_image_asset, "sources"))
            source_entries.append(
                SettingsTransferSourceEntry(
                    key=str(source.key),
                    name=str(source.name),
                    url=str(source.base_url),
                    adapter_key=(str(getattr(source, "adapter_key", "") or "").strip() or None),
                    parser_config=dict(getattr(source, "parser_config", None) or {}),
                    sort_priority=int(getattr(setting, "sort_priority", 0) or 0) or 1,
                    enabled=bool(getattr(setting, "is_enabled", True)),
                    sync_enabled=bool(getattr(setting, "is_sync_enabled", True)),
                    dedup_enabled=bool(getattr(setting, "dedup_enabled", True)),
                    hide_auto_added_products=bool(getattr(setting, "hide_auto_added_products", False)),
                    description_mode=str(getattr(setting, "description_mode", "text") or "text"),
                    show_images=bool(getattr(setting, "show_images", True)),
                    supplier_key=(
                        str(supplier_by_id[int(setting.supplier_id)].key)
                        if getattr(setting, "supplier_id", None) is not None and int(setting.supplier_id) in supplier_by_id
                        else None
                    ),
                    promo_factor=float(getattr(setting, "promo_factor", 1.0) or 1.0),
                    promo_only_no_discount=bool(getattr(setting, "promo_only_no_discount", False)),
                    buyout_surcharge_value=(
                        float(setting.buyout_surcharge_value)
                        if getattr(setting, "buyout_surcharge_value", None) is not None
                        else None
                    ),
                    buyout_surcharge_currency=(
                        _normalize_currency(getattr(setting, "buyout_surcharge_currency", None), default="RUB")
                        if getattr(setting, "buyout_surcharge_currency", None)
                        else None
                    ),
                    logo_asset_checksum=(
                        str(source.logo_image_asset.checksum_sha256)
                        if getattr(source, "logo_image_asset", None) is not None
                        else None
                    ),
                )
            )

        weight_entries = [
            SettingsTransferWeightRuleEntry(
                weight_grams=int(rule.weight_grams),
                keywords=[
                    str(item.keyword)
                    for item in self.weight_rule_repo.list_keywords(int(rule.id))
                ],
            )
            for rule in weight_rules
        ]
        if getattr(showcase_setting, "desktop_hero_image_asset", None) is not None:
            export_assets.append((showcase_setting.desktop_hero_image_asset, "showcase"))
        if getattr(showcase_setting, "mobile_hero_image_asset", None) is not None:
            export_assets.append((showcase_setting.mobile_hero_image_asset, "showcase"))
        for row in showcase_media_settings:
            if getattr(row, "image_asset", None) is not None:
                export_assets.append((row.image_asset, "showcase"))
        site_content = self._export_site_content()
        for checksum in site_content.about.photo_asset_checksums:
            asset = (
                self.db.query(ImageAsset)
                .filter(
                    ImageAsset.scope == SiteContentService.ASSET_SCOPE,
                    ImageAsset.checksum_sha256 == str(checksum or "").strip(),
                )
                .one_or_none()
            )
            if asset is not None:
                export_assets.append((asset, SiteContentService.ASSET_SCOPE))
        for notification in site_content.notifications:
            if notification.image_asset_checksum:
                asset = (
                    self.db.query(ImageAsset)
                    .filter(
                        ImageAsset.scope == SiteContentService.ASSET_SCOPE,
                        ImageAsset.checksum_sha256 == str(notification.image_asset_checksum or "").strip(),
                    )
                    .one_or_none()
                )
                if asset is not None:
                    export_assets.append((asset, SiteContentService.ASSET_SCOPE))

        return SettingsTransferPayload(
            schema_version=_SCHEMA_VERSION,
            exported_at=datetime.now(timezone.utc).isoformat(),
            project=_PROJECT_NAME,
            pricing_settings=pricing,
            admin_ui_settings=admin_ui,
            suppliers=supplier_entries,
            sources=source_entries,
            weight_rules=weight_entries,
            designers=[
                SettingsTransferDesignerEntry(
                    name=str(row.name),
                    slug=str(row.slug),
                    description=(str(row.description) if row.description else None),
                    origin_kind=str(getattr(row, "origin_kind", "manual") or "manual"),
                    is_admin_touched=bool(getattr(row, "is_admin_touched", False)),
                    is_enabled=bool(getattr(row, "is_enabled", True)),
                )
                for row in designer_rows
                if str(row.name or "").strip() and str(row.slug or "").strip()
            ],
            designer_source_names=[
                SettingsTransferDesignerSourceNameEntry(
                    source_name=str(row.source_name),
                    designer_name=(str(row.designer_name) if row.designer_name is not None else None),
                    is_enabled=bool(getattr(row, "is_enabled", True)),
                    is_admin_touched=bool(getattr(row, "is_admin_touched", False)),
                )
                for row in mapping_rows
            ],
            taxonomy=self._export_taxonomy(),
            showcase_media=SettingsTransferShowcaseMedia(
                desktop_hero_asset_checksum=(
                    str(showcase_setting.desktop_hero_image_asset.checksum_sha256)
                    if getattr(showcase_setting, "desktop_hero_image_asset", None) is not None
                    else None
                ),
                mobile_hero_asset_checksum=(
                    str(showcase_setting.mobile_hero_image_asset.checksum_sha256)
                    if getattr(showcase_setting, "mobile_hero_image_asset", None) is not None
                    else None
                ),
                desktop_carousel=[
                    SettingsTransferShowcaseCarouselEntry(
                        asset_checksum=str(row.image_asset.checksum_sha256),
                        viewport="desktop",
                        position=int(row.position),
                    )
                    for row in showcase_media_settings
                    if getattr(row, "image_asset", None) is not None and str(getattr(row, "viewport", "") or "") == "desktop"
                ],
                mobile_carousel=[
                    SettingsTransferShowcaseCarouselEntry(
                        asset_checksum=str(row.image_asset.checksum_sha256),
                        viewport="mobile",
                        position=int(row.position),
                    )
                    for row in showcase_media_settings
                    if getattr(row, "image_asset", None) is not None and str(getattr(row, "viewport", "") or "") == "mobile"
                ],
            ),
            site_content=site_content,
            image_assets=self._export_image_assets(export_assets),
        )

    def import_payload(self, payload: SettingsTransferPayload) -> SettingsTransferResponse:
        if int(payload.schema_version) != _SCHEMA_VERSION:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported schema_version: {payload.schema_version}",
            )

        asset_map: dict[tuple[str, str], ImageAsset] = {}
        created_assets: list[ImageAsset] = []
        try:
            asset_map, created_assets = self._import_image_assets(payload.image_assets)
            supplier_map = self._import_suppliers(payload.suppliers)
            pricing_updated = self._import_pricing(payload.pricing_settings)
            admin_ui_updated = self._import_admin_ui(payload.admin_ui_settings)
            designer_count = self._import_designers(payload.designers)
            designer_source_names_updated = self._import_designer_source_names(payload.designer_source_names)
            source_count = self._import_sources(payload.sources, supplier_map=supplier_map, asset_map=asset_map)
            weight_count = self._import_weight_rules(payload.weight_rules)
            taxonomy_state = self._import_taxonomy(payload.taxonomy)
            showcase_media_updated = self._import_showcase_media(payload.showcase_media, asset_map=asset_map)
            site_content_updated = self._import_site_content(payload.site_content, asset_map=asset_map)
            DesignerCatalogSyncService(self.db).reconcile(sync_product_links=True)
            pruned_source_count = self._prune_sources(payload.sources)
            pruned_supplier_count = self._prune_suppliers(payload.suppliers)
            pruned_designer_count = self._prune_designers(payload.designers)

            self.db.commit()
            return SettingsTransferResponse(
                ok=True,
                message="Настройки импортированы",
                schema_version=_SCHEMA_VERSION,
                imported_at=datetime.now(timezone.utc).isoformat(),
                imported_counts={
                    "pricing_settings_updated": pricing_updated,
                    "admin_ui_settings_updated": admin_ui_updated,
                    "suppliers_upserted": len(supplier_map),
                    "image_assets_upserted": len(asset_map),
                    "sources_upserted": source_count,
                    "sources_deleted": pruned_source_count,
                    "weight_rules_replaced": weight_count,
                    "designers_upserted": designer_count,
                    "designers_deleted": pruned_designer_count,
                    "designer_source_names_replaced": designer_source_names_updated,
                    "suppliers_deleted": pruned_supplier_count,
                    "taxonomy_filters_replaced": len(taxonomy_state.filters),
                    "taxonomy_custom_catalogs_replaced": len(taxonomy_state.custom_catalogs),
                    "showcase_categories_replaced": len(taxonomy_state.showcase_categories),
                    "showcase_media_assets_linked": showcase_media_updated,
                    "site_access_settings_updated": site_content_updated["access"],
                    "site_about_photos_linked": site_content_updated["about_photos"],
                    "site_notifications_replaced": site_content_updated["notifications"],
                    "site_questions_replaced": site_content_updated["questions"],
                },
            )
        except HTTPException:
            self.db.rollback()
            self._cleanup_created_assets(created_assets)
            raise
        except Exception:
            self.db.rollback()
            self._cleanup_created_assets(created_assets)
            raise

    def reset_all(self) -> SettingsTransferResponse:
        seed = DefaultAdminSettingsLoader.load()

        # 1) Reset pricing settings/suppliers to packaged defaults.
        self.db.query(SupplierShippingRate).delete(synchronize_session=False)
        self.db.query(Supplier).delete(synchronize_session=False)
        self.db.query(PricingSetting).delete(synchronize_session=False)
        self.db.query(AdminUiSettings).delete(synchronize_session=False)
        self.db.flush()

        pricing_service = PricingSettingsService(self.db)
        pricing_service.reset_to_seed()
        CatalogDefaultsService(self.db).ensure()
        self.db.flush()
        suppliers = self.supplier_repo.list_all_with_rates()
        fallback_supplier = next((s for s in suppliers if str(getattr(s, "key", "") or "") == seed.default_source_supplier_key), None)
        source_defaults = seed.source_setting_defaults

        # 2) Reset sources to packaged defaults.
        sources_reset = 0
        for source in self.source_repo.list_all():
            if str(source.key) == SourceRegistryService.MANUAL_SOURCE_KEY:
                continue
            setting = self.source_repo.ensure_setting(source)
            setting.is_enabled = bool(source_defaults.enabled)
            setting.is_sync_enabled = bool(source_defaults.sync_enabled)
            setting.dedup_enabled = bool(source_defaults.dedup_enabled)
            setting.hide_auto_added_products = bool(source_defaults.hide_auto_added_products)
            setting.description_mode = str(source_defaults.description_mode)
            setting.show_images = bool(source_defaults.show_images)
            setting.promo_factor = float(source_defaults.promo_factor)
            setting.promo_only_no_discount = bool(source_defaults.promo_only_no_discount)
            setting.buyout_surcharge_value = source_defaults.buyout_surcharge_value
            setting.buyout_surcharge_currency = source_defaults.buyout_surcharge_currency
            if fallback_supplier is not None:
                setting.supplier_id = int(fallback_supplier.id)
            sources_reset += 1

        # 3) Reset weight rules.
        self.db.query(WeightRuleKeyword).delete(synchronize_session=False)
        self.db.query(WeightRule).delete(synchronize_session=False)
        self.db.flush()
        weight_rule_count = 0

        # 4) Reset designer source names.
        self.db.query(DesignerSourceName).delete(synchronize_session=False)
        self.db.query(SiteAboutPhoto).delete(synchronize_session=False)
        self.db.query(SiteQuestionItem).delete(synchronize_session=False)
        about = self.db.query(SiteAboutSetting).filter(SiteAboutSetting.id == 1).one_or_none()
        if about is None:
            about = SiteAboutSetting(id=1, body_text="")
            self.db.add(about)
        else:
            about.body_text = ""
        access = self.db.query(SiteAccessSetting).filter(SiteAccessSetting.id == 1).one_or_none()
        if access is None:
            access = SiteAccessSetting(id=1)
            self.db.add(access)
        access.enabled = False
        access.title = ""
        access.description = ""
        access.password_value = ""
        access.password_hash = ""
        access.session_version = int(access.session_version or 1) + 1

        self.db.commit()
        return SettingsTransferResponse(
            ok=True,
            message="Настройки сброшены к значениям по умолчанию",
            schema_version=_SCHEMA_VERSION,
            imported_at=datetime.now(timezone.utc).isoformat(),
            imported_counts={
                "pricing_settings_updated": 1,
                "admin_ui_settings_updated": 1,
                "suppliers_upserted": len(suppliers),
                "sources_upserted": sources_reset,
                "weight_rules_replaced": weight_rule_count,
                "site_access_settings_updated": 1,
            },
        )

    def _import_image_assets(self, assets: list[SettingsTransferImageAssetEntry]) -> tuple[dict[tuple[str, str], ImageAsset], list[ImageAsset]]:
        result: dict[tuple[str, str], ImageAsset] = {}
        created_assets: list[ImageAsset] = []
        seen_asset_keys: set[tuple[str, str]] = set()
        for item in assets:
            scope = self.media_assets.normalize_scope(item.scope)
            checksum = str(item.checksum_sha256 or "").strip()
            asset_key = (scope, checksum)
            if not checksum or asset_key in seen_asset_keys:
                continue
            seen_asset_keys.add(asset_key)
            existing = (
                self.db.query(ImageAsset)
                .filter(
                    ImageAsset.scope == scope,
                    ImageAsset.checksum_sha256 == checksum,
                )
                .one_or_none()
            )
            if existing is not None:
                result[asset_key] = existing
                continue
            try:
                content = base64.b64decode(item.content_base64.encode("ascii"), validate=True)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Не удалось прочитать медиафайл {checksum}: {exc}",
                ) from exc
            actual_checksum = sha256(content).hexdigest()
            if actual_checksum != checksum:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Контрольная сумма медиафайла не совпадает для {checksum}.",
                )
            asset = self.media_assets.save_bytes(
                scope=scope,
                file_name=str(item.file_name or "").strip() or f"{checksum}.bin",
                content=content,
            )
            result[asset_key] = asset
            created_assets.append(asset)
        return result, created_assets

    def _cleanup_created_assets(self, assets: list[ImageAsset]) -> None:
        for asset in assets:
            file_path = self.media_assets.resolve_file_path(asset)
            if file_path.exists():
                file_path.unlink()

    @staticmethod
    def _require_asset_checksum(
        *,
        asset_map: dict[tuple[str, str], ImageAsset],
        checksum: str,
        expected_scope: str,
        field_label: str,
    ) -> ImageAsset:
        scope = MediaAssetService.normalize_scope(expected_scope)
        normalized = str(checksum or "").strip()
        asset = asset_map.get((scope, normalized))
        if asset is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"В файле отсутствует изображение для поля '{field_label}'.",
            )
        return asset

    def _import_designers(self, rows: list[SettingsTransferDesignerEntry]) -> int:
        existing_by_slug = {
            str(row.slug).strip(): row
            for row in self.db.query(Designer).order_by(Designer.id.asc()).all()
            if str(row.slug or "").strip()
        }
        existing_by_name = {
            self._normalize_designer_key(getattr(row, "name", None)): row
            for row in existing_by_slug.values()
            if self._normalize_designer_key(getattr(row, "name", None))
        }
        kept_ids: set[int] = set()
        for item in rows:
            slug = str(item.slug or "").strip()
            name = " ".join(str(item.name or "").strip().split())
            if not slug or not name:
                continue
            entity = existing_by_slug.get(slug)
            if entity is None:
                entity = existing_by_name.get(self._normalize_designer_key(name))
            if entity is None:
                entity = Designer(
                    name=name,
                    slug=slug,
                    description=item.description,
                    origin_kind=item.origin_kind,
                    is_admin_touched=bool(item.is_admin_touched),
                    is_enabled=bool(item.is_enabled),
                )
                self.db.add(entity)
                self.db.flush()
            else:
                entity.name = name
                entity.slug = slug
                entity.description = item.description
                entity.origin_kind = str(item.origin_kind or "manual")
                entity.is_admin_touched = bool(item.is_admin_touched)
                entity.is_enabled = bool(item.is_enabled)
            existing_by_slug[slug] = entity
            existing_by_name[self._normalize_designer_key(name)] = entity
            kept_ids.add(int(entity.id))
        return len(kept_ids)

    def _import_taxonomy(self, payload: SettingsTransferTaxonomyState) -> TaxonomyState:
        weight_rule_id_by_grams = {
            int(rule.weight_grams): int(rule.id)
            for rule in self.weight_rule_repo.list_active()
            if rule.id is not None and rule.weight_grams is not None
        }
        state = TaxonomyState(
            filters=self._import_taxonomy_filters(
                payload.filters,
                weight_rule_id_by_grams=weight_rule_id_by_grams,
            ),
            custom_catalogs=[
                TaxonomyCustomCatalog(
                    slug=str(item.slug).strip(),
                    title=item.title,
                    description=item.description,
                    is_enabled=item.is_enabled,
                    product_ids=[],
                )
                for item in payload.custom_catalogs
            ],
            showcase_categories=[
                TaxonomyShowcaseCategory(
                    code=str(category.code),
                    title=category.title,
                    attachments=[
                        TaxonomyShowcaseAttachment(
                            kind=attachment.kind,
                            filter_slug=attachment.filter_slug,
                            custom_catalog_slug=attachment.custom_catalog_slug,
                            hidden_filter_slugs=[str(value) for value in attachment.hidden_filter_slugs],
                        )
                        for attachment in category.attachments
                    ],
                )
                for category in payload.showcase_categories
            ],
        )
        return self.taxonomy.replace_state(state)

    def _import_showcase_media(
        self,
        payload: SettingsTransferShowcaseMedia,
        *,
        asset_map: dict[tuple[str, str], ImageAsset],
    ) -> int:
        settings = self.db.query(ShowcaseSetting).order_by(ShowcaseSetting.id.asc()).first()
        if settings is None:
            settings = ShowcaseSetting(id=1)
            self.db.add(settings)
            self.db.flush()
        desktop_hero_checksum = str(payload.desktop_hero_asset_checksum or "").strip()
        mobile_hero_checksum = str(payload.mobile_hero_asset_checksum or "").strip()
        desktop_carousel = sorted(payload.desktop_carousel, key=lambda row: int(row.position))
        mobile_carousel = sorted(payload.mobile_carousel, key=lambda row: int(row.position))

        linked = 0
        if desktop_hero_checksum:
            linked += 1
        if mobile_hero_checksum:
            linked += 1
        linked += len({str(item.asset_checksum or "").strip() for item in desktop_carousel if str(item.asset_checksum or "").strip()})
        linked += len({str(item.asset_checksum or "").strip() for item in mobile_carousel if str(item.asset_checksum or "").strip()})

        state = ShowcaseService(self.db).replace_state(
            ShowcaseStateUpdateRequest(
                desktop={
                    "hero_asset_id": (
                        int(self._require_asset_checksum(
                            asset_map=asset_map,
                            checksum=desktop_hero_checksum,
                            expected_scope="showcase",
                            field_label="Компьютерная заставка",
                        ).id)
                        if desktop_hero_checksum else None
                    ),
                    "carousel_asset_ids": [
                        int(
                            self._require_asset_checksum(
                                asset_map=asset_map,
                                checksum=str(item.asset_checksum or "").strip(),
                                expected_scope="showcase",
                                field_label=f"Компьютерная карусель #{index}",
                            ).id
                        )
                        for index, item in enumerate(desktop_carousel, start=1)
                        if str(item.asset_checksum or "").strip()
                    ],
                },
                mobile={
                    "hero_asset_id": (
                        int(self._require_asset_checksum(
                            asset_map=asset_map,
                            checksum=mobile_hero_checksum,
                            expected_scope="showcase",
                            field_label="Мобильная заставка",
                        ).id)
                        if mobile_hero_checksum else None
                    ),
                    "carousel_asset_ids": [
                        int(
                            self._require_asset_checksum(
                                asset_map=asset_map,
                                checksum=str(item.asset_checksum or "").strip(),
                                expected_scope="showcase",
                                field_label=f"Мобильная карусель #{index}",
                            ).id
                        )
                        for index, item in enumerate(mobile_carousel, start=1)
                        if str(item.asset_checksum or "").strip()
                    ],
                },
            )
        )
        self.db.flush()
        return (
            (1 if state.desktop.hero_asset is not None else 0)
            + (1 if state.mobile.hero_asset is not None else 0)
            + len(state.desktop.carousel_assets)
            + len(state.mobile.carousel_assets)
        )

    def _import_site_content(
        self,
        payload: SettingsTransferSiteContent,
        *,
        asset_map: dict[tuple[str, str], ImageAsset],
    ) -> dict[str, int]:
        access = self.db.query(SiteAccessSetting).order_by(SiteAccessSetting.id.asc()).first()
        if access is None:
            access = SiteAccessSetting(id=1)
            self.db.add(access)
            self.db.flush()
        access_password = str(payload.access.password or "").strip()
        access.enabled = bool(payload.access.enabled)
        access.title = str(payload.access.title or "").strip()
        access.description = str(payload.access.description or "").strip()
        access.password_value = access_password
        access.password_hash = hash_password(access_password) if access_password else ""
        access.session_version = int(access.session_version or 1) + 1

        about = self.db.query(SiteAboutSetting).order_by(SiteAboutSetting.id.asc()).first()
        if about is None:
            about = SiteAboutSetting(id=1)
            self.db.add(about)
            self.db.flush()
        about.body_text = str(payload.about.text or "")

        self.db.query(SiteAboutPhoto).delete(synchronize_session=False)
        self.db.flush()
        linked_about_photos = 0
        seen_checksums: set[str] = set()
        for position, checksum in enumerate(payload.about.photo_asset_checksums, start=1):
            normalized_checksum = str(checksum or "").strip()
            if not normalized_checksum or normalized_checksum in seen_checksums:
                continue
            seen_checksums.add(normalized_checksum)
            asset = self._require_asset_checksum(
                asset_map=asset_map,
                checksum=normalized_checksum,
                expected_scope=SiteContentService.ASSET_SCOPE,
                field_label=f"Фото 'Обо мне' #{position}",
            )
            self.db.add(SiteAboutPhoto(image_asset_id=int(asset.id), position=linked_about_photos + 1))
            linked_about_photos += 1

        self.db.query(SiteQuestionItem).delete(synchronize_session=False)
        self.db.flush()
        linked_questions = 0
        for item in sorted(payload.questions, key=lambda row: int(row.position)):
            self.db.add(
                SiteQuestionItem(
                    question=str(item.question or ""),
                    answer=str(item.answer or ""),
                    is_enabled=bool(item.is_enabled),
                    is_expanded_by_default=bool(item.is_expanded_by_default),
                    position=linked_questions + 1,
                )
            )
            linked_questions += 1
        self.db.flush()
        self.db.query(SiteNotificationSetting).delete(synchronize_session=False)
        self.db.flush()
        linked_notifications = 0
        for item in sorted(payload.notifications, key=lambda row: int(row.position)):
            image_asset_id = None
            if item.image_asset_checksum:
                notification_asset = self._require_asset_checksum(
                    asset_map=asset_map,
                    checksum=str(item.image_asset_checksum or "").strip(),
                    expected_scope=SiteContentService.ASSET_SCOPE,
                    field_label=f"Фото уведомления #{linked_notifications + 1}",
                )
                image_asset_id = int(notification_asset.id)
            self.db.add(
                SiteNotificationSetting(
                    title=str(item.title or ""),
                    description=str(item.description or ""),
                    button_text=str(item.button_text or ""),
                    button_url=str(item.button_url or ""),
                    version=max(1, int(item.version or 1)),
                    image_asset_id=image_asset_id,
                )
            )
            linked_notifications += 1
        self.db.flush()
        return {
            "access": 1,
            "about_photos": linked_about_photos,
            "notifications": linked_notifications,
            "questions": linked_questions,
        }

    def _prune_sources(self, rows: list[SettingsTransferSourceEntry]) -> int:
        desired_keys = {
            SourceRegistryService.normalize_source_key(item.key)
            for item in rows
            if SourceRegistryService.normalize_source_key(item.key)
        }
        stale_sources = [
            source
            for source in self.db.query(Source).order_by(Source.id.asc()).all()
            if str(source.key or "") != SourceRegistryService.MANUAL_SOURCE_KEY and str(source.key or "") not in desired_keys
        ]
        if not stale_sources:
            return 0

        listing_counts = {
            int(source_id): int(count)
            for source_id, count in (
                self.db.query(ProductListing.source_id, func.count(ProductListing.id))
                .filter(ProductListing.source_id.in_([int(source.id) for source in stale_sources]))
                .group_by(ProductListing.source_id)
                .all()
            )
        }

        deleted = 0
        for source in stale_sources:
            linked_listings = listing_counts.get(int(source.id), 0)
            if linked_listings > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"В файле отсутствует источник '{source.name}', но к нему все еще привязано {linked_listings} листингов.",
                )
            self.db.delete(source)
            deleted += 1
        self.db.flush()
        return deleted

    def _prune_suppliers(self, rows: list[SettingsTransferSupplierEntry]) -> int:
        desired_keys = {
            _normalize_supplier_key(item.key, item.name, index=index)
            for index, item in enumerate(rows, start=1)
        }
        stale_suppliers = [
            supplier
            for supplier in self.db.query(Supplier).order_by(Supplier.id.asc()).all()
            if str(supplier.key or "") not in desired_keys
        ]
        if not stale_suppliers:
            return 0

        assignment_counts = {
            int(supplier_id): int(count)
            for supplier_id, count in (
                self.db.query(SourceSetting.supplier_id, func.count(SourceSetting.source_id))
                .filter(SourceSetting.supplier_id.in_([int(supplier.id) for supplier in stale_suppliers]))
                .group_by(SourceSetting.supplier_id)
                .all()
            )
            if supplier_id is not None
        }

        deleted = 0
        for supplier in sorted(stale_suppliers, key=lambda item: (0 if item.parent_supplier_id is not None else 1, int(item.id))):
            assigned_sources = assignment_counts.get(int(supplier.id), 0)
            if assigned_sources > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"В файле отсутствует тариф '{supplier.name}', но он все еще назначен на {assigned_sources} источников.",
                )
            self.db.delete(supplier)
            deleted += 1
        self.db.flush()
        return deleted

    def _prune_designers(self, rows: list[SettingsTransferDesignerEntry]) -> int:
        desired_slugs = {str(item.slug or "").strip() for item in rows if str(item.slug or "").strip()}
        desired_names = {self._normalize_designer_key(item.name) for item in rows if self._normalize_designer_key(item.name)}
        referenced_designer_ids = {
            int(designer_id)
            for designer_id, in self.db.query(Product.designer_id).filter(Product.designer_id.is_not(None)).all()
        }
        referenced_designer_ids.update(
            int(designer_id)
            for designer_id, in self.db.query(DesignerSourceName.designer_id).filter(DesignerSourceName.designer_id.is_not(None)).all()
        )

        deleted = 0
        for designer in self.db.query(Designer).order_by(Designer.id.asc()).all():
            if int(designer.id) in referenced_designer_ids:
                continue
            if str(designer.slug or "").strip() in desired_slugs:
                continue
            if self._normalize_designer_key(designer.name) in desired_names:
                continue
            self.db.delete(designer)
            deleted += 1
        self.db.flush()
        return deleted

    def _import_pricing(self, payload: SettingsTransferPricingSettings) -> int:
        values = payload.model_dump()
        updated_fields = 0
        numeric_fields = {
            "markup_multiplier",
            "weight_tolerance",
            "customs_threshold_eur",
            "customs_duty_rate",
            "eur_to_usd_rate",
            "gbp_to_usd_rate",
            "jpy_to_usd_rate",
            "eur_to_rub_rate",
            "usd_to_rub_rate",
            "usdt_to_rub_rate",
            "usdt_extra_rub",
            "payment_fee_rate",
            "customs_processing_rate",
            "customs_fixed_rub",
            "tax_rate",
        }
        normalized_svc_rules = PricingSettingsService._normalize_svc_rules(values.get("svc_rules"))
        PricingSettingsService._validate_svc_rules_no_overlap(normalized_svc_rules)
        mapped_values = {
            "markup_multiplier": float(values["markup_multiplier"]),
            "weight_tolerance": float(values["weight_tolerance"]),
            "customs_threshold_eur": float(values["customs_threshold_eur"]),
            "customs_duty_rate": float(values["customs_duty_rate"]),
            "eur_to_usd_rate": float(values["eur_to_usd_rate"]),
            "gbp_to_usd_rate": float(values["gbp_to_usd_rate"]),
            "jpy_to_usd_rate": float(values["jpy_to_usd_rate"]),
            "eur_to_rub_rate": float(values["eur_to_rub_rate"]),
            "usd_to_rub_rate": float(values["usd_to_rub_rate"]),
            "usdt_to_rub_rate": float(values["usdt_to_rub_rate"]),
            "usdt_extra_rub": float(values["usdt_extra_rub"]),
            "final_rounding_mode": str(values["final_rounding_mode"]),
            "payment_fee_rate": float(values["payment_fee_rate"]),
            "customs_processing_rate": float(values["customs_processing_rate"]),
            "customs_fixed_rub": float(values["customs_fixed_rub"]),
            "tax_rate": float(values["tax_rate"]),
            "svc_rules": normalized_svc_rules,
        }
        row = self.pricing_repo.get_singleton()
        if row is None:
            row = PricingSetting(
                id=1,
                markup_multiplier=mapped_values["markup_multiplier"],
                weight_tolerance=mapped_values["weight_tolerance"],
                customs_threshold_eur=mapped_values["customs_threshold_eur"],
                customs_duty_rate=mapped_values["customs_duty_rate"],
                eur_to_usd_rate=mapped_values["eur_to_usd_rate"],
                gbp_to_usd_rate=mapped_values["gbp_to_usd_rate"],
                jpy_to_usd_rate=mapped_values["jpy_to_usd_rate"],
                eur_to_rub_rate=mapped_values["eur_to_rub_rate"],
                usd_to_rub_rate=mapped_values["usd_to_rub_rate"],
                usdt_to_rub_rate=mapped_values["usdt_to_rub_rate"],
                usdt_extra_rub=mapped_values["usdt_extra_rub"],
                final_rounding_mode=mapped_values["final_rounding_mode"],
                payment_fee_rate=mapped_values["payment_fee_rate"],
                customs_processing_rate=mapped_values["customs_processing_rate"],
                customs_fixed_rub=mapped_values["customs_fixed_rub"],
                tax_rate=mapped_values["tax_rate"],
                svc_rules=mapped_values["svc_rules"],
                bybit_bucket_rates=[],
            )
            self.db.add(row)
            self.db.flush()
            return len(mapped_values)
        for key, raw_value in mapped_values.items():
            current_value = getattr(row, key)
            value_changed = (
                not _numeric_values_equal(current_value, raw_value)
                if key in numeric_fields
                else current_value != raw_value
            )
            if value_changed:
                setattr(row, key, raw_value)
                updated_fields += 1
        return updated_fields

    def _import_admin_ui(self, payload: SettingsTransferAdminUiSettings) -> int:
        row = self.db.query(AdminUiSettings).filter(AdminUiSettings.id == 1).one_or_none()
        if row is None:
            row = AdminUiSettings(id=1)
            self.db.add(row)
            self.db.flush()
        updated_fields = 0
        values = payload.model_dump()
        normalized = {
            "auto_sync_period_minutes": int(values["auto_sync_period_minutes"]),
        }
        for key, raw_value in normalized.items():
            if getattr(row, key) != raw_value:
                setattr(row, key, raw_value)
                updated_fields += 1
        return updated_fields

    def _import_suppliers(self, suppliers: list[SettingsTransferSupplierEntry]) -> dict[str, Supplier]:
        existing = {str(item.key): item for item in self.supplier_repo.list_all_with_rates()}
        result: dict[str, Supplier] = {}
        incoming_by_key: dict[str, SettingsTransferSupplierEntry] = {}

        for index, incoming in enumerate(suppliers, start=1):
            key = _normalize_supplier_key(incoming.key, incoming.name, index=index)
            incoming_by_key[key] = incoming
            current = existing.get(key)
            if current is None:
                current = self.supplier_repo.create(
                    key=key,
                    name=incoming.name,
                    provider_kind="alternate" if incoming.provider_kind in {"alternate", "alt"} else "main",
                    rate_currency=_normalize_currency(incoming.rate_currency, default="RUB"),
                    is_enabled=bool(incoming.is_enabled),
                )
                self.db.flush()
            else:
                current.name = incoming.name
                current.provider_kind = "alternate" if incoming.provider_kind in {"alternate", "alt"} else "main"
                current.rate_currency = _normalize_currency(incoming.rate_currency, default="RUB")
                current.is_enabled = bool(incoming.is_enabled)
            self.supplier_repo.replace_ranges(
                supplier_id=int(current.id),
                ranges=[
                    {
                        "min_kg": max(0.0, float(getattr(rate, "min_kg", 0.0))),
                        "max_kg": getattr(rate, "max_kg", None),
                        "rub": max(0.0, float(getattr(rate, "rub", 0.0))),
                    }
                    for rate in incoming.rates
                ],
            )
            result[key] = current

        # Apply parent/alt linkage after all suppliers exist.
        for key, supplier in result.items():
            incoming = incoming_by_key.get(key)
            if incoming is None:
                continue
            parent_key = _normalize_supplier_key(
                incoming.parent_supplier_key or "",
                incoming.parent_supplier_key or "",
                index=0,
            ) if incoming.parent_supplier_key else None
            parent_supplier = result.get(parent_key) if parent_key else None
            supplier.parent_supplier_id = int(parent_supplier.id) if parent_supplier is not None else None
            supplier.provider_kind = "alternate" if supplier.parent_supplier_id is not None else "main"
        return result

    def _import_sources(
        self,
        sources: list[SettingsTransferSourceEntry],
        *,
        supplier_map: dict[str, Supplier],
        asset_map: dict[tuple[str, str], ImageAsset],
    ) -> int:
        if not supplier_map:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нет тарифов для назначения источникам")
        updated = 0
        for item in sources:
            source_key = SourceRegistryService.normalize_source_key(item.key)
            if not source_key:
                continue
            if source_key == SourceRegistryService.MANUAL_SOURCE_KEY:
                existing = SourceRegistryService(self.db).ensure_manual_source()
            else:
                existing = self.source_repo.get_by_key(source_key)
                if existing is None:
                    existing = self.db.query(Source).filter(Source.key == source_key).one_or_none()
            supplier = supplier_map.get(item.supplier_key) if item.supplier_key else None
            if item.supplier_key and supplier is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Для источника '{item.name}' не найден назначенный тариф",
                )
            supplier_id = int(supplier.id) if supplier is not None else None
            if existing is None:
                existing = self.source_repo.create(
                    key=source_key,
                    name=item.name,
                    base_url=item.url,
                    adapter_key=item.adapter_key,
                    parser_config=dict(item.parser_config or {}),
                )
            else:
                existing.name = item.name
                existing.base_url = item.url
                existing.base_url_normalized = normalize_base_url(item.url)
                existing.adapter_key = (str(item.adapter_key).strip() or None) if item.adapter_key is not None else None
                existing.parser_config = dict(item.parser_config or {})
            setting = self.source_repo.ensure_setting(existing)
            self.source_repo.ensure_sync_state(existing)
            setting.is_enabled = bool(item.enabled)
            setting.is_sync_enabled = bool(item.sync_enabled)
            setting.dedup_enabled = bool(item.dedup_enabled)
            setting.hide_auto_added_products = bool(item.hide_auto_added_products)
            setting.description_mode = str(item.description_mode)
            setting.show_images = bool(item.show_images)
            setting.sort_priority = int(item.sort_priority)
            setting.supplier_id = supplier_id
            setting.promo_factor = float(item.promo_factor)
            setting.promo_only_no_discount = bool(item.promo_only_no_discount)
            setting.buyout_surcharge_value = (
                float(item.buyout_surcharge_value)
                if item.buyout_surcharge_value is not None
                else None
            )
            setting.buyout_surcharge_currency = (
                _normalize_currency(item.buyout_surcharge_currency, default="RUB")
                if item.buyout_surcharge_currency is not None
                else None
            )
            logo_checksum = str(item.logo_asset_checksum or "").strip()
            existing.logo_image_asset_id = (
                int(self._require_asset_checksum(asset_map=asset_map, checksum=logo_checksum, expected_scope="sources", field_label=f"Логотип источника '{item.name}'").id)
                if logo_checksum
                else None
            )
            updated += 1
        return updated

    def _import_weight_rules(self, rules: list[SettingsTransferWeightRuleEntry]) -> int:
        self.db.query(WeightRuleKeyword).delete(synchronize_session=False)
        self.db.query(WeightRule).delete(synchronize_session=False)
        self.db.flush()

        count = 0
        for item in rules:
            created = WeightRule(
                weight_grams=max(1, int(item.weight_grams)),
                is_enabled=True,
            )
            self.db.add(created)
            self.db.flush()
            unique_keywords = sorted({keyword.strip().lower() for keyword in item.keywords if keyword and keyword.strip()})
            for keyword in unique_keywords:
                self.db.add(
                    WeightRuleKeyword(
                        rule_id=int(created.id),
                        keyword=keyword,
                    )
                )
            count += 1
        return count

    def _import_designer_source_names(self, rows: list[SettingsTransferDesignerSourceNameEntry]) -> int:
        self.db.query(DesignerSourceName).delete(synchronize_session=False)
        self.db.flush()
        inserted = 0
        designers_by_name = {
            self._normalize_designer_key(getattr(designer, "name", None)): designer
            for designer in self.db.query(Designer).order_by(Designer.id.asc()).all()
            if self._normalize_designer_key(getattr(designer, "name", None))
        }
        seen_source_names: set[str] = set()

        for item in rows:
            source_name = str(item.source_name or "").strip()
            if not source_name:
                continue
            if source_name in seen_source_names:
                continue
            seen_source_names.add(source_name)

            designer_id = None
            designer_name = " ".join(str(item.designer_name or "").strip().split())
            if designer_name:
                designer = designers_by_name.get(self._normalize_designer_key(designer_name))
                if designer is None:
                    designer = Designer(
                        name=designer_name,
                        slug=_slugify_name(designer_name),
                        origin_kind="manual",
                        is_admin_touched=True,
                        is_enabled=True,
                    )
                    self.db.add(designer)
                    self.db.flush()
                    designers_by_name[self._normalize_designer_key(designer_name)] = designer
                designer_id = int(designer.id)

            self.db.add(
                DesignerSourceName(
                    source_name=source_name,
                    designer_name=(designer_name or source_name),
                    designer_id=designer_id,
                    is_enabled=bool(item.is_enabled),
                    is_admin_touched=bool(item.is_admin_touched),
                )
            )
            inserted += 1
        return inserted
