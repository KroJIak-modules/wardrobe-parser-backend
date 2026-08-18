"""Export/import of admin settings (without product data)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    AdminRole,
    AdminUiSettings,
    PricingSetting,
    ProductListing,
    SiteAboutSetting,
    SiteAccessSetting,
    SiteNotificationSetting,
    SiteQuestionItem,
    Source,
    SourceSetting,
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
from app.services.catalog.catalog_defaults_service import CatalogDefaultsService
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
    SettingsTransferPayload,
    SettingsTransferPricingSettings,
    SettingsTransferResponse,
    SettingsTransferRoleEntry,
    SettingsTransferSourceEntry,
    SettingsTransferSupplierEntry,
    SettingsTransferTaxonomyCustomCatalog,
    SettingsTransferTaxonomyFilterNode,
    SettingsTransferTaxonomyShowcaseAttachment,
    SettingsTransferTaxonomyShowcaseCategory,
    SettingsTransferTaxonomyState,
    SettingsTransferSiteAccess,
    SettingsTransferSiteAbout,
    SettingsTransferSiteContent,
    SettingsTransferSiteNotification,
    SettingsTransferSiteQuestionItem,
    SettingsTransferWeightRuleEntry,
)
from app.services.auth.passwords import hash_password
from app.services.auth.permissions import normalize_permission_list

_SCHEMA_VERSION = 11
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
        self.taxonomy = TaxonomyService(db)

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
                restrict_by_gender=bool(node.restrict_by_gender),
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
        return SettingsTransferSiteContent(
            access=SettingsTransferSiteAccess(
                enabled=bool(getattr(access, "enabled", False)),
                title=str(getattr(access, "title", "") or ""),
                description=str(getattr(access, "description", "") or ""),
                password=str(getattr(access, "password_value", "") or ""),
            ),
            about=SettingsTransferSiteAbout(text=str(getattr(about, "body_text", "") or "")),
            notifications=[
                SettingsTransferSiteNotification(
                    title=str(getattr(notification, "title", "") or ""),
                    description=str(getattr(notification, "description", "") or ""),
                    button_text=str(getattr(notification, "button_text", "") or ""),
                    button_url=str(getattr(notification, "button_url", "") or ""),
                    version=int(getattr(notification, "version", 1) or 1),
                    position=index,
                )
                for index, notification in enumerate(notifications, start=1)
            ],
            questions=[
                SettingsTransferSiteQuestionItem(
                    question=str(item.question or ""), answer=str(item.answer or ""),
                    is_enabled=bool(item.is_enabled),
                    is_expanded_by_default=bool(item.is_expanded_by_default), position=int(item.position),
                )
                for item in self.db.query(SiteQuestionItem).order_by(SiteQuestionItem.position.asc(), SiteQuestionItem.id.asc()).all()
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
                restrict_by_gender=node.restrict_by_gender,
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
        supplier_by_id = {int(supplier.id): supplier for supplier in suppliers}
        role_rows = self.db.query(AdminRole).order_by(AdminRole.name.asc(), AdminRole.id.asc()).all()
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
        for source in sources:
            setting = self.source_repo.ensure_setting(source)
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
                    clean_public_titles=bool(getattr(setting, "clean_public_titles", False)),
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
        return SettingsTransferPayload(
            schema_version=_SCHEMA_VERSION,
            exported_at=datetime.now(timezone.utc).isoformat(),
            project=_PROJECT_NAME,
            pricing_settings=pricing,
            admin_ui_settings=admin_ui,
            roles=[
                SettingsTransferRoleEntry(
                    name=str(role.name),
                    description=(str(role.description) if role.description is not None else None),
                    permissions=normalize_permission_list(role.permissions),
                )
                for role in role_rows
                if str(role.name or "").strip()
            ],
            suppliers=supplier_entries,
            sources=source_entries,
            weight_rules=weight_entries,
            taxonomy=self._export_taxonomy(),
            site_content=self._export_site_content(),
        )

    def import_payload(self, payload: SettingsTransferPayload) -> SettingsTransferResponse:
        if int(payload.schema_version) != _SCHEMA_VERSION:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported schema_version: {payload.schema_version}",
            )

        try:
            supplier_map = self._import_suppliers(payload.suppliers)
            pricing_updated = self._import_pricing(payload.pricing_settings)
            admin_ui_updated = self._import_admin_ui(payload.admin_ui_settings)
            roles_updated = self._import_roles(payload.roles)
            source_count = self._import_sources(payload.sources, supplier_map=supplier_map)
            weight_count = self._import_weight_rules(payload.weight_rules)
            taxonomy_state = self._import_taxonomy(payload.taxonomy)
            site_content_updated = self._import_site_content(payload.site_content)
            pruned_source_count = self._prune_sources(payload.sources)
            pruned_supplier_count = self._prune_suppliers(payload.suppliers)

            self.db.commit()
            # A transfer replaces several singleton rows; expire the identity map
            # so callers immediately read their committed state.
            self.db.expire_all()
            return SettingsTransferResponse(
                ok=True,
                message="Настройки импортированы",
                schema_version=_SCHEMA_VERSION,
                imported_at=datetime.now(timezone.utc).isoformat(),
                imported_counts={
                    "pricing_settings_updated": pricing_updated,
                    "admin_ui_settings_updated": admin_ui_updated,
                    "roles_upserted": roles_updated,
                    "suppliers_upserted": len(supplier_map),
                    "sources_upserted": source_count,
                    "sources_deleted": pruned_source_count,
                    "weight_rules_replaced": weight_count,
                    "suppliers_deleted": pruned_supplier_count,
                    "taxonomy_filters_replaced": len(taxonomy_state.filters),
                    "taxonomy_custom_catalogs_replaced": len(taxonomy_state.custom_catalogs),
                    "showcase_categories_replaced": len(taxonomy_state.showcase_categories),
                    "site_access_settings_updated": site_content_updated["access"],
                    "site_notifications_replaced": site_content_updated["notifications"],
                    "site_questions_replaced": site_content_updated["questions"],
                },
            )
        except HTTPException:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
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
            setting.clean_public_titles = bool(source_defaults.clean_public_titles)
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

        # Designer mappings remain catalog data and are intentionally outside reset.
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
        return self.taxonomy.replace_state(state, commit=False)

    def _import_site_content(self, payload: SettingsTransferSiteContent) -> dict[str, int]:
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

        self.db.query(SiteQuestionItem).delete(synchronize_session=False)
        self.db.flush()
        for position, item in enumerate(sorted(payload.questions, key=lambda row: int(row.position)), start=1):
            self.db.add(SiteQuestionItem(
                question=str(item.question or ""), answer=str(item.answer or ""),
                is_enabled=bool(item.is_enabled), is_expanded_by_default=bool(item.is_expanded_by_default),
                position=position,
            ))
        self.db.flush()
        self.db.query(SiteNotificationSetting).delete(synchronize_session=False)
        self.db.flush()
        for item in sorted(payload.notifications, key=lambda row: int(row.position)):
            self.db.add(SiteNotificationSetting(
                title=str(item.title or ""), description=str(item.description or ""),
                button_text=str(item.button_text or ""), button_url=str(item.button_url or ""),
                version=max(1, int(item.version or 1)),
            ))
        self.db.flush()
        return {"access": 1, "notifications": len(payload.notifications), "questions": len(payload.questions)}

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

    def _import_roles(self, roles: list[SettingsTransferRoleEntry]) -> int:
        """Upsert portable authorization policies without copying user identities."""
        existing_by_name = {
            str(role.name or "").strip(): role
            for role in self.db.query(AdminRole).order_by(AdminRole.id.asc()).all()
            if str(role.name or "").strip()
        }
        imported = 0
        for item in roles:
            name = str(item.name or "").strip()
            if not name:
                continue
            role = existing_by_name.get(name)
            if role is None:
                role = AdminRole(name=name)
                self.db.add(role)
                existing_by_name[name] = role
            role.description = (str(item.description) if item.description is not None else None)
            role.permissions = normalize_permission_list(item.permissions)
            imported += 1
        self.db.flush()
        return imported

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
            setting.clean_public_titles = bool(item.clean_public_titles)
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
