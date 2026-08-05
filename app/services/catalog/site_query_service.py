from __future__ import annotations

import re

from sqlalchemy import func, select, union_all
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models import (
    Designer,
    ImageAsset,
    Product,
    ProductListing,
    ProductListingMember,
    ProductListingVariant,
    Source,
    SourceSetting,
)
from app.schemas.site import (
    SiteAboutResponse,
    SiteCarouselItemResponse,
    SiteCarouselResponse,
    SiteNavigationCatalogContextEntry,
    SiteNavigationCatalogContexts,
    SiteCatalogExperienceResponse,
    SiteCatalogFilterGroup,
    SiteCatalogFilterOption,
    SiteCatalogHeader,
    SiteCatalogProductBrand,
    SiteCatalogProductResponse,
    SiteCatalogProductsResponse,
    SiteDesignerEntryResponse,
    SiteDesignersResponse,
    SiteHeroResponse,
    SiteMediaAssetResponse,
    SiteMobileMenuRootGroup,
    SiteNavigationMenu,
    SiteNavigationMenuColumn,
    SiteNavigationMenuColumnTitle,
    SiteNavigationMenuEntry,
    SiteNavigationMobileMenu,
    SiteNavigationResponse,
    SiteNavigationTopSection,
    SiteProductDescriptionResponse,
    SiteProductRecommendationContextResponse,
    SiteProductResponse,
    SiteProductSourceResponse,
    SiteProductVariantResponse,
    SiteQuestionsResponse,
    SiteRouteTarget,
)
from app.services.catalog.admin_showcase_preview_service import AdminShowcasePreviewService
from app.services.catalog.designer_support import slugify_designer_name
from app.services.catalog.product_query_service import ProductQueryService
from app.services.catalog.product_title_service import ProductTitleService
from app.services.catalog.showcase_service import ShowcaseService
from app.services.catalog.site_content_service import SiteContentService
from app.services.catalog.taxonomy_service import TaxonomyService


_PRODUCT_PATH_RE = re.compile(r"^(?P<id>\d+)(?:-(?P<handle>.*))?$")


class SiteQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.showcase = ShowcaseService(db)
        self.preview = AdminShowcasePreviewService(db)
        self.products = ProductQueryService(db)
        self.taxonomy = TaxonomyService(db)
        self.content = SiteContentService(db)

    @staticmethod
    def _normalize_handle(value: object | None) -> str:
        return slugify_designer_name(str(value or "").strip())

    @classmethod
    def build_product_path(cls, product_id: int, title: object | None) -> str:
        handle = cls._normalize_handle(title)
        return f"{int(product_id)}-{handle}" if handle else str(int(product_id))

    @staticmethod
    def _map_public_gender(value: object | None) -> str | None:
        normalized = str(value or "").strip().lower()
        if normalized == "male":
            return "men"
        if normalized == "unisex":
            return "men"
        if normalized == "female":
            return "women"
        return None

    @staticmethod
    def _split_csv_values(values: list[str]) -> list[str]:
        result: list[str] = []
        for raw_value in values:
            result.extend([item.strip() for item in str(raw_value or "").split(",") if item.strip()])
        return result

    @classmethod
    def _normalized_catalog_search_params(cls, search_params: dict[str, list[str]]) -> dict[str, list[str]]:
        normalized: dict[str, list[str]] = {
            str(key): [str(item) for item in values]
            for key, values in search_params.items()
        }
        has_gender = bool(cls._split_csv_values(normalized.get("gender") or []))
        top_value = str((normalized.get("top") or [None])[0] or "").strip().lower()
        if not has_gender and top_value in {"men", "women"}:
            normalized["gender"] = [top_value]
        return normalized

    @staticmethod
    def _site_status(*, availability_mode: object | None, orderability_status: object | None) -> str:
        if str(orderability_status or "").strip().lower() == "sold_out":
            return "sold_out"
        if str(availability_mode or "").strip().lower() == "in_stock":
            return "in_stock"
        return "preorder"

    @staticmethod
    def _public_variant_title(value: object | None) -> str:
        title = str(value or "").strip()
        if title.casefold() == "default title":
            return "Базовый вариант"
        return title or "ONE SIZE"

    @staticmethod
    def _normalize_catalog_sort(value: object | None) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"price_asc", "price-asc"}:
            return "price_asc"
        if normalized in {"price_desc", "price-desc"}:
            return "price_desc"
        return "featured"

    @staticmethod
    def _target_payload(target: dict | None) -> SiteRouteTarget | None:
        if not isinstance(target, dict):
            return None
        pathname = str(target.get("pathname") or "").strip()
        if not pathname:
            return None
        raw_query = target.get("query")
        has_designer_query = isinstance(raw_query, dict) and bool(str(raw_query.get("designer") or "").strip())
        if pathname == "/catalog/sale":
            pathname = "/sale"
        if pathname == "/catalog/designers":
            pathname = "/catalog" if has_designer_query else "/designers"
        query: dict[str, str | list[str]] | None = None
        if isinstance(raw_query, dict):
            query = {}
            for key, value in raw_query.items():
                normalized_key = str(key or "").strip()
                if not normalized_key:
                    continue
                if isinstance(value, list):
                    query[normalized_key] = [str(item) for item in value if str(item or "").strip()]
                elif value is not None and str(value or "").strip():
                    query[normalized_key] = str(value)
            if not query:
                query = None
        return SiteRouteTarget(pathname=pathname, query=query)

    @staticmethod
    def _menu_entry_from_item(item: dict) -> SiteNavigationMenuEntry:
        return SiteNavigationMenuEntry(
            id=str(item.get("id") or ""),
            label=str(item.get("label") or ""),
            presentation="heading" if str(item.get("presentation") or "").strip().lower() == "heading" else "item",
            description=(str(item.get("description")).strip() if item.get("description") else None),
            target=SiteQueryService._target_payload(item.get("target") if isinstance(item, dict) else None),
        )

    @staticmethod
    def _filter_node_id_from_entry_id(raw: object, prefix: str) -> int | None:
        value = str(raw or "").strip()
        if not value.startswith(prefix):
            return None
        try:
            node_id = int(value.removeprefix(prefix))
        except ValueError:
            return None
        return node_id if node_id > 0 else None

    @classmethod
    def _flatten_admin_filter_nodes(cls, nodes: list[dict]) -> dict[int, dict]:
        result: dict[int, dict] = {}
        for node in nodes:
            if not isinstance(node, dict):
                continue
            try:
                node_id = int(node.get("id") or 0)
            except (TypeError, ValueError):
                node_id = 0
            if node_id > 0:
                result[node_id] = node
            result.update(cls._flatten_admin_filter_nodes(node.get("children") if isinstance(node.get("children"), list) else []))
        return result

    def _ordered_desktop_category_entries(self, block: dict) -> list[SiteNavigationMenuEntry]:
        block_id = str(block.get("id") or "").strip()
        root_id = self._filter_node_id_from_entry_id(block_id, "filter-")
        if root_id is None:
            return []

        filters_by_id = self._flatten_admin_filter_nodes(
            self.preview.taxonomy_state.get("filters") if isinstance(self.preview.taxonomy_state.get("filters"), list) else []
        )
        root = filters_by_id.get(root_id)
        if root is None:
            return []

        item_by_node_id: dict[int, SiteNavigationMenuEntry] = {}
        fallback_entries: list[SiteNavigationMenuEntry] = []
        for item in block.get("items") or []:
            if not isinstance(item, dict):
                continue
            entry = self._menu_entry_from_item(item)
            node_id = self._filter_node_id_from_entry_id(item.get("id"), "filter-item-")
            if node_id is None:
                fallback_entries.append(entry)
                continue
            item_by_node_id[node_id] = entry

        group_entries_by_node_id: dict[int, list[SiteNavigationMenuEntry]] = {}
        for group in block.get("groups") or []:
            if not isinstance(group, dict):
                continue
            group_id = self._filter_node_id_from_entry_id(group.get("id"), "filter-group-")
            if group_id is None:
                continue
            entries: list[SiteNavigationMenuEntry] = []
            title = str(group.get("title") or "").strip()
            if title:
                entries.append(
                    SiteNavigationMenuEntry(
                        id=f"{block_id}-{group.get('id')}-heading",
                        label=title,
                        presentation="heading",
                        target=self._target_payload(group.get("titleTarget")),
                    )
                )
            entries.extend(
                self._menu_entry_from_item(item)
                for item in group.get("items") or []
                if isinstance(item, dict)
            )
            group_entries_by_node_id[group_id] = entries

        ordered: list[SiteNavigationMenuEntry] = []
        used_item_ids: set[int] = set()
        used_group_ids: set[int] = set()
        direct_children = root.get("children") if isinstance(root.get("children"), list) else []
        for child in direct_children:
            if not isinstance(child, dict):
                continue
            try:
                child_id = int(child.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if child_id in group_entries_by_node_id:
                ordered.extend(group_entries_by_node_id[child_id])
                used_group_ids.add(child_id)
            if child_id in item_by_node_id:
                ordered.append(item_by_node_id[child_id])
                used_item_ids.add(child_id)

        ordered.extend(entry for node_id, entry in item_by_node_id.items() if node_id not in used_item_ids)
        for node_id, entries in group_entries_by_node_id.items():
            if node_id not in used_group_ids:
                ordered.extend(entries)
        ordered.extend(fallback_entries)
        return ordered

    def _desktop_menu_from_section(self, section: dict) -> SiteNavigationMenu | None:
        menu = section.get("menu") if isinstance(section, dict) else None
        if not isinstance(menu, dict):
            return None
        key = str(section.get("key") or "").strip().lower()
        if key not in {"new", "designers", "men", "women"}:
            return None
        columns: list[SiteNavigationMenuColumn] = []
        for block in menu.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            entries = self._ordered_desktop_category_entries(block)
            if not entries:
                entries = [self._menu_entry_from_item(item) for item in block.get("items") or [] if isinstance(item, dict)]
                for group in block.get("groups") or []:
                    if not isinstance(group, dict):
                        continue
                    title = str(group.get("title") or "").strip()
                    if title:
                        entries.append(
                            SiteNavigationMenuEntry(
                                id=f"{block.get('id')}-{group.get('id')}-heading",
                                label=title,
                                presentation="heading",
                                target=self._target_payload(group.get("titleTarget")),
                            )
                        )
                    entries.extend(
                        [self._menu_entry_from_item(item) for item in group.get("items") or [] if isinstance(item, dict)]
                    )
            columns.append(
                SiteNavigationMenuColumn(
                    id=str(block.get("id") or f"{key}-{len(columns) + 1}"),
                    align="center" if key == "designers" else "start",
                    title=(
                        SiteNavigationMenuColumnTitle(
                            label=str(block.get("title") or ""),
                            target=self._target_payload(block.get("titleTarget")),
                        )
                        if str(block.get("title") or "").strip()
                        else None
                    ),
                    entries=entries,
                )
            )
        footer_link = None
        footer = menu.get("footerLink")
        if isinstance(footer, dict):
            footer_link = SiteNavigationMenuEntry(
                id=f"{key}-footer",
                label=str(footer.get("label") or ""),
                presentation="item",
                target=self._target_payload(footer.get("target")),
            )
        return SiteNavigationMenu(
            key=key,  # type: ignore[arg-type]
            columns=columns,
            footer_link=footer_link,
        )

    def _catalog_contexts_payload(self) -> SiteNavigationCatalogContexts:
        custom_catalogs = [
            SiteNavigationCatalogContextEntry(
                slug=item.slug,
                label=item.label,
                description=item.description,
            )
            for item in self.preview._build_catalog_header_custom_catalogs()
        ]
        designers = [
            SiteNavigationCatalogContextEntry(
                slug=item.id,
                label=item.label,
                description=item.catalog_description,
            )
            for item in self.preview._build_catalog_header_designers()
        ]
        return SiteNavigationCatalogContexts(
            designers=designers,
            custom_catalogs=custom_catalogs,
        )

    def navigation(self) -> SiteNavigationResponse:
        raw = self.preview.navigation()
        sections = raw.get("sections") if isinstance(raw, dict) else []
        desktop_menus: dict[str, SiteNavigationMenu] = {}
        top_sections: list[SiteNavigationTopSection] = []
        for section in sections or []:
            if not isinstance(section, dict):
                continue
            key = str(section.get("key") or "").strip().lower()
            if key not in {"new", "designers", "men", "women", "sale"}:
                continue
            top_sections.append(
                SiteNavigationTopSection(
                    key=key,  # type: ignore[arg-type]
                    label=str(section.get("label") or ""),
                    target=self._target_payload(section.get("target")),
                )
            )
            desktop_menu = self._desktop_menu_from_section(section)
            if desktop_menu is not None:
                desktop_menus[key] = desktop_menu
        return SiteNavigationResponse(
            top_sections=top_sections,
            desktop_menus=desktop_menus,
            mobile_menu=self._mobile_menu_payload(desktop_menus),
            catalog_contexts=self._catalog_contexts_payload(),
        )

    def _mobile_menu_payload(self, desktop_menus: dict[str, SiteNavigationMenu]) -> SiteNavigationMobileMenu:
        # Desktop category menus already embody the admin-configured showcase
        # attachments, visibility, ordering and gender scope. The mobile-only
        # pair setting only changes presentation: it joins two configured root
        # columns without rebuilding entries from taxonomy.
        filters_by_id = self._flatten_admin_filter_nodes(
            self.preview.taxonomy_state.get("filters") if isinstance(self.preview.taxonomy_state.get("filters"), list) else []
        )
        paired_root_ids: dict[int, int] = {}
        for root_id, node in filters_by_id.items():
            try:
                pair_id = int(node.get("mobile_pair_root_id") or 0)
            except (TypeError, ValueError):
                continue
            paired_node = filters_by_id.get(pair_id)
            try:
                reciprocal_pair_id = int(paired_node.get("mobile_pair_root_id") or 0) if paired_node is not None else 0
            except (TypeError, ValueError):
                reciprocal_pair_id = 0
            if pair_id > 0 and reciprocal_pair_id == root_id:
                paired_root_ids[root_id] = pair_id

        grouped_menus: dict[str, list[SiteMobileMenuRootGroup]] = {}
        for gender in ("men", "women"):
            menu = desktop_menus.get(gender)
            if menu is None:
                continue
            columns_by_root_id = {
                root_id: column
                for column in menu.columns
                if (root_id := self._filter_node_id_from_entry_id(column.id, "filter-")) is not None
            }
            used_column_ids: set[str] = set()
            groups: list[SiteMobileMenuRootGroup] = []
            for column in menu.columns:
                if column.id in used_column_ids:
                    continue
                entries = list(column.entries)
                if not entries and column.title is None:
                    continue
                root_id = self._filter_node_id_from_entry_id(column.id, "filter-")
                pair_id = paired_root_ids.get(root_id) if root_id is not None else None
                paired_column = columns_by_root_id.get(pair_id) if pair_id is not None else None
                if paired_column is not None and paired_column.id not in used_column_ids:
                    pair_entries = list(paired_column.entries)
                    labels = [
                        item.title.label if item.title is not None else (item.entries[0].label if item.entries else item.id)
                        for item in (column, paired_column)
                    ]
                    groups.append(
                        SiteMobileMenuRootGroup(
                            id=f"mobile-pair:{column.id}:{paired_column.id}",
                            label=" и ".join(labels),
                            entries=[*entries, *pair_entries],
                        )
                    )
                    used_column_ids.add(paired_column.id)
                    used_column_ids.add(column.id)
                    continue
                groups.append(
                    SiteMobileMenuRootGroup(
                        id=column.id,
                        label=column.title.label if column.title is not None else (entries[0].label if entries else column.id),
                        entries=entries,
                    )
                )
                used_column_ids.add(column.id)
            if groups:
                grouped_menus[gender] = groups
        return SiteNavigationMobileMenu(groups_by_gender=grouped_menus)

    def home_hero(self, viewport: str) -> SiteHeroResponse:
        state = self.showcase.state()
        viewport_state = state.desktop if viewport == "desktop" else state.mobile
        asset = viewport_state.hero_asset
        return SiteHeroResponse(
            viewport=viewport,  # type: ignore[arg-type]
            asset=(
                SiteMediaAssetResponse(
                    id=int(asset.id),
                    url=f"/api/v1/site/media/{int(asset.id)}/file",
                    media_kind=asset.media_kind,
                    mime_type=asset.mime_type,
                    byte_size=int(asset.byte_size),
                    width_px=asset.width_px,
                    height_px=asset.height_px,
                )
                if asset is not None
                else None
            ),
        )

    def home_carousel(self, viewport: str) -> SiteCarouselResponse:
        state = self.showcase.state()
        viewport_state = state.desktop if viewport == "desktop" else state.mobile
        return SiteCarouselResponse(
            viewport=viewport,  # type: ignore[arg-type]
            items=[
                SiteCarouselItemResponse(
                    id=int(asset.id),
                    position=index,
                    url=f"/api/v1/site/media/{int(asset.id)}/file",
                    media_kind=asset.media_kind,
                    mime_type=asset.mime_type,
                    byte_size=int(asset.byte_size),
                    width_px=asset.width_px,
                    height_px=asset.height_px,
                )
                for index, asset in enumerate(viewport_state.carousel_assets, start=1)
            ],
        )

    def catalog_experience(self, *, view_key: str, search_params: dict[str, list[str]]) -> SiteCatalogExperienceResponse:
        normalized_search_params = self._normalized_catalog_search_params(search_params)
        payload = self.preview.catalog_experience(view_key=view_key, search_params=normalized_search_params)
        view = payload.get("view") if isinstance(payload, dict) else {}
        header = view.get("header") if isinstance(view, dict) else {}
        raw_groups = payload.get("filterGroups") if isinstance(payload, dict) else []
        filter_groups: list[SiteCatalogFilterGroup] = []
        for group in raw_groups or []:
            if not isinstance(group, dict):
                continue
            filter_groups.append(
                SiteCatalogFilterGroup(
                    key=str(group.get("key") or ""),
                    label=str(group.get("label") or ""),
                    query_param=str(group.get("queryParam") or ""),
                    selection_mode=str(group.get("selectionMode") or "single"),  # type: ignore[arg-type]
                    options=[
                        SiteCatalogFilterOption(
                            id=str(option.get("id") or ""),
                            label=str(option.get("label") or ""),
                            value=str(option.get("value") or ""),
                        )
                        for option in group.get("options") or []
                        if isinstance(option, dict)
                    ],
                    panel_width=(
                        str(group.get("panelWidth"))
                        if str(group.get("panelWidth") or "").strip() in {"compact", "wide"}
                        else None
                    ),
                    max_visible_options=(
                        int(group.get("maxVisibleOptions"))
                        if group.get("maxVisibleOptions") is not None
                        else None
                    ),
                    prioritize_selected=(
                        bool(group.get("prioritizeSelected"))
                        if group.get("prioritizeSelected") is not None
                        else None
                    ),
                )
            )
        return SiteCatalogExperienceResponse(
            header=SiteCatalogHeader(
                title=str(header.get("title") or ""),
                description=(str(header.get("description")) if header.get("description") else None),
                source=str(header.get("source") or "catalog"),  # type: ignore[arg-type]
            ),
            filter_groups=filter_groups,
        )

    def _designer_id_from_slug(self, slug: str | None) -> int | None:
        normalized_slug = str(slug or "").strip()
        if not normalized_slug:
            return None
        row = self.db.query(Designer.id).filter(Designer.slug == normalized_slug).one_or_none()
        return int(row[0]) if row is not None else None

    def _filtered_public_product_ids(
        self,
        *,
        query: str,
        designer_slugs: list[str],
        gender_values: list[str],
        filter_slugs: list[str],
        custom_catalog_slug: str | None,
        availability_mode: str | None,
        orderability_status: str | None,
        discounted_only: bool,
    ):
        normalized_designer_ids = sorted(
            {
                designer_id
                for designer_id in [self._designer_id_from_slug(slug) for slug in self._split_csv_values(designer_slugs)]
                if designer_id is not None
            }
        )
        if designer_slugs and not normalized_designer_ids:
            return select(Product.id.label("product_id")).where(False).subquery("site_public_product_ids")
        normalized_filter_slugs = sorted(
            {str(slug or "").strip() for slug in self._split_csv_values(filter_slugs) if str(slug or "").strip()}
        )

        mapped_genders = []
        for gender in self._split_csv_values(gender_values):
            normalized = str(gender or "").strip().lower()
            if normalized == "men":
                mapped_genders.extend(["male", "unisex"])
            elif normalized == "women":
                mapped_genders.append("female")
        normalized_genders = sorted(set(mapped_genders))
        gender_subqueries = normalized_genders or [None]
        designer_subqueries = normalized_designer_ids or [None]
        filter_subqueries = normalized_filter_slugs or [None]
        subqueries = [
            self.products._base_product_id_query(
                query=query,
                source_id=None,
                source_mode=None,
                designer_filter=str(designer_id) if designer_id is not None else None,
                gender=gender_value,
                filter_slug=filter_slug,
                custom_catalog_slug=custom_catalog_slug,
                visibility_status="visible",
                availability_mode=availability_mode,
                orderability_status=orderability_status,
                audience="public",
            )
            for gender_value in gender_subqueries
            for designer_id in designer_subqueries
            for filter_slug in filter_subqueries
        ]
        combined = union_all(*[query_item.statement for query_item in subqueries]).subquery("site_public_union")
        filtered_ids = select(combined.c.product_id).distinct().subquery("site_public_product_ids")
        if not discounted_only:
            return filtered_ids

        discounted_ids = (
            select(ProductListingMember.product_id.label("product_id"))
            .join(ProductListingVariant, ProductListingVariant.listing_id == ProductListingMember.listing_id)
            .where(ProductListingVariant.price_amount.is_not(None))
            .where(ProductListingVariant.compare_at_price_amount.is_not(None))
            .where(ProductListingVariant.compare_at_price_amount > ProductListingVariant.price_amount)
            .distinct()
            .subquery("site_public_discounted_product_ids")
        )
        return (
            select(filtered_ids.c.product_id)
            .join(discounted_ids, discounted_ids.c.product_id == filtered_ids.c.product_id)
            .distinct()
            .subquery("site_public_filtered_discounted_product_ids")
        )

    def catalog_products(
        self,
        *,
        limit: int,
        offset: int,
        query: str,
        designer_slugs: list[str],
        gender_values: list[str],
        filter_slugs: list[str],
        custom_catalog_slug: str | None,
        availability_mode: str | None,
        orderability_status: str | None,
        discounted_only: bool,
        sort: str | None = None,
    ) -> SiteCatalogProductsResponse:
        effective_orderability_status = str(orderability_status or "").strip().lower() or "orderable"
        if effective_orderability_status not in {"orderable", "sold_out"}:
            effective_orderability_status = "orderable"
        normalized_sort = self._normalize_catalog_sort(sort)
        filtered_ids = self._filtered_public_product_ids(
            query=query,
            designer_slugs=designer_slugs,
            gender_values=gender_values,
            filter_slugs=filter_slugs,
            custom_catalog_slug=custom_catalog_slug,
            availability_mode=availability_mode,
            orderability_status=effective_orderability_status,
            discounted_only=bool(discounted_only),
        )
        total = int(self.db.query(func.count()).select_from(filtered_ids).scalar() or 0)
        base_query = (
            self.db.query(filtered_ids.c.product_id)
            .join(Product, Product.id == filtered_ids.c.product_id)
            .join(ProductListing, ProductListing.id == Product.primary_listing_id)
            .outerjoin(Source, Source.id == ProductListing.source_id)
            .outerjoin(SourceSetting, SourceSetting.source_id == ProductListing.source_id)
        )
        if normalized_sort == "price_asc":
            ordered_query = base_query.order_by(
                Product.site_sort_price_rub.asc().nullslast(),
                *self.products._default_product_sorting_expressions(),
            )
        elif normalized_sort == "price_desc":
            ordered_query = base_query.order_by(
                Product.site_sort_price_rub.desc().nullslast(),
                *self.products._default_product_sorting_expressions(),
            )
        else:
            ordered_query = self.products._apply_default_product_sorting(base_query)
        product_ids = [
            int(row[0])
            for row in ordered_query
            .offset(max(0, int(offset)))
            .limit(max(1, int(limit)))
            .all()
        ]
        product_rows = self.products.products.list_site_catalog_card_rows_by_ids(product_ids)
        row_by_product_id = {int(row.product_id): row for row in product_rows}
        products_by_id = {
            int(product.id): product
            for product in self.products.products.list_products_by_ids(product_ids, include_merged=False)
        }
        items = []
        for product_id in product_ids:
            row = row_by_product_id.get(product_id)
            if row is None:
                continue
            items.append(self._site_catalog_product_response(row, product=products_by_id.get(product_id)))
        return SiteCatalogProductsResponse(items=items, total=total, limit=int(limit), offset=int(offset))

    @staticmethod
    def _site_catalog_brand_name(row) -> str:
        return str(getattr(row, "designer_name", "") or "").strip()

    @classmethod
    def _site_catalog_title(cls, row) -> str:
        title_override = str(getattr(row, "title_override", "") or "").strip()
        if title_override:
            return title_override
        source_title = str(getattr(row, "source_title", "") or "").strip()
        if source_title:
            return (
                ProductTitleService.public_title(
                    source_title=source_title,
                    source_designer_name=cls._site_catalog_brand_name(row),
                    source_category_name=str(getattr(row, "source_category_raw", "") or "").strip() or None,
                    clean=bool(getattr(row, "clean_public_titles", False)),
                )
                or source_title
            )
        return f"Product {int(getattr(row, 'product_id'))}"

    @staticmethod
    def _site_catalog_image_url(row) -> str | None:
        gallery_image_url = str(getattr(row, "gallery_image_url", "") or "").strip()
        if gallery_image_url:
            return gallery_image_url
        if getattr(row, "gallery_scope_product_id", None) is not None:
            return None
        if getattr(row, "show_images", None) is False:
            return None
        source_image_url = str(getattr(row, "source_image_url", "") or "").strip()
        return source_image_url or None

    def _site_catalog_old_price_rub(self, row, *, product: Product | None) -> int | None:
        compare_at_price = getattr(row, "compare_at_price_amount", None)
        price = getattr(row, "price_amount", None)
        display_price = getattr(row, "site_sort_price_rub", None)
        if (
            compare_at_price is None
            or price is None
            or display_price is None
            or float(price) <= 0
            or float(compare_at_price) <= float(price)
        ):
            return None

        # The current price is cached for catalogue sorting. Recalculate the
        # prior source price through the same pricing service so both public
        # prices share every rule, including configured final rounding.
        listing_id = getattr(row, "representative_listing_id", None)
        listing = next(
            (membership.listing for membership in (product.memberships if product is not None else []) if membership.listing_id == listing_id),
            None,
        )
        if product is None or listing is None:
            return None
        old_price_rub, _ = self.products._compute_variant_pricing(
            listing,
            source_price=float(compare_at_price),
            source_currency=str(getattr(row, "currency_code", "") or "").upper() or None,
            compare_at_price=None,
            weight_grams=self.products._effective_weight_grams(product, listing),
            pricing_mode=str(getattr(row, "pricing_mode", "") or "source"),
        )
        return int(round(old_price_rub)) if old_price_rub is not None and old_price_rub > float(display_price) else None

    def _site_catalog_product_response(self, row, *, product: Product | None) -> SiteCatalogProductResponse:
        title = self._site_catalog_title(row)
        effective_orderability_status = (
            "unavailable"
            if str(getattr(row, "dedup_status", "") or "independent").strip().lower() != "independent"
            else str(getattr(row, "orderability_status", "") or "unavailable").strip().lower() or "unavailable"
        )
        status = self._site_status(
            availability_mode=getattr(row, "availability_mode", None),
            orderability_status=effective_orderability_status,
        )
        return SiteCatalogProductResponse(
            id=int(getattr(row, "product_id")),
            path=self.build_product_path(int(getattr(row, "product_id")), title),
            brand=SiteCatalogProductBrand(
                name=self._site_catalog_brand_name(row),
                slug=(str(getattr(row, "designer_slug", "") or "") or None),
            ),
            name=title,
            price_rub=(
                int(round(float(getattr(row, "site_sort_price_rub"))))
                if getattr(row, "site_sort_price_rub", None) is not None
                else None
            ),
            old_price_rub=self._site_catalog_old_price_rub(row, product=product),
            status=status,  # type: ignore[arg-type]
            image_url=self._site_catalog_image_url(row),
        )

    def designers(self) -> SiteDesignersResponse:
        payload = self.preview.designers_directory()
        return SiteDesignersResponse(
            alphabet=[str(item) for item in payload.get("alphabet") or []],
            entries=[
                SiteDesignerEntryResponse(
                    slug=str(entry.get("slug") or ""),
                    label=str(entry.get("label") or ""),
                    letter=str(entry.get("letter") or "#"),
                )
                for entry in payload.get("entries") or []
                if isinstance(entry, dict)
            ],
        )

    def product(self, product_path: str) -> SiteProductResponse:
        match = _PRODUCT_PATH_RE.match(str(product_path or "").strip())
        if match is None:
            raise NotFoundError("Товар не найден")
        product_id = int(match.group("id"))
        product = self.products.products.get_product(product_id)
        if product is None:
            raise NotFoundError("Товар не найден")
        public_payload = self.products.get_product_payload(product_id, audience="public")
        if public_payload is None:
            raise NotFoundError("Товар не найден")
        admin_payload = self.products.get_admin_mutation_payload(product_id) or {}
        listing_by_id = {
            int(membership.listing.id): membership.listing
            for membership in product.memberships
            if membership.listing is not None
        }
        status = self._site_status(
            availability_mode=public_payload.get("availability_mode"),
            orderability_status=public_payload.get("orderability_status"),
        )
        description_content = str(public_payload.get("description") or "").strip()
        description_mode = "hidden" if str(public_payload.get("description_mode") or "text").strip().lower() == "hidden" else "text"
        description = None
        if description_content and description_mode == "text":
            description = SiteProductDescriptionResponse(
                format="text",
                content=description_content,
            )
        variants: list[SiteProductVariantResponse] = []
        for variant in public_payload.get("variants") or []:
            if not isinstance(variant, dict) or not bool(variant.get("available")):
                continue
            source_id = int(variant.get("source_id")) if variant.get("source_id") is not None else 0
            listing = listing_by_id.get(int(variant.get("listing_id") or 0))
            source = getattr(listing, "source", None) if listing is not None else None
            logo_asset_id = int(source.logo_image_asset_id) if source is not None and getattr(source, "logo_image_asset_id", None) is not None else None
            if source is not None and getattr(source, "logo_image_asset_id", None) is not None:
                logo_asset_id = int(source.logo_image_asset_id)
            variants.append(
                SiteProductVariantResponse(
                    id=int(variant.get("id") or 0),
                    size=self._public_variant_title(variant.get("title")),
                    price_rub=(
                        int(round(float(variant.get("final_price"))))
                        if variant.get("final_price") is not None
                        else None
                    ),
                    old_price_rub=(
                        int(round(float(variant.get("final_compare_at_price"))))
                        if variant.get("final_compare_at_price") is not None
                        else None
                    ),
                    source=SiteProductSourceResponse(
                        id=source_id,
                        name=str(variant.get("source_name") or ""),
                        url=(str(listing.url) if listing is not None else None),
                        logo_url=(
                            f"/api/v1/sources/images/{logo_asset_id}"
                            if logo_asset_id is not None and logo_asset_id > 0
                            else None
                        ),
                    ),
                )
            )
        taxonomy = admin_payload.get("taxonomy") if isinstance(admin_payload.get("taxonomy"), dict) else {}
        section_slug = None
        filter_slugs = taxonomy.get("filter_slugs") if isinstance(taxonomy.get("filter_slugs"), list) else []
        if filter_slugs:
            section_slug = str(filter_slugs[0] or "").strip() or None
        return SiteProductResponse(
            id=product_id,
            path=self.build_product_path(product_id, public_payload.get("title")),
            handle=self._normalize_handle(public_payload.get("title")),
            brand=SiteCatalogProductBrand(
                name=str(public_payload.get("brand_name") or ""),
                slug=(str(getattr(product.designer, "slug", "") or "") or None),
            ),
            name=str(public_payload.get("title") or ""),
            description=description,
            status=status,  # type: ignore[arg-type]
            photos=[str(url) for url in public_payload.get("image_urls") or []],
            variants=variants,
            primary_source_url=(str(public_payload.get("url")) if public_payload.get("url") else None),
            recommendation_context=SiteProductRecommendationContextResponse(
                designer_slug=(str(getattr(product.designer, "slug", "") or "") or None),
                section_slug=section_slug,
                gender=self._map_public_gender(public_payload.get("gender")),  # type: ignore[arg-type]
            ),
        )

    def about(self) -> SiteAboutResponse:
        return self.content.get_public_about()

    def questions(self) -> SiteQuestionsResponse:
        return self.content.get_public_questions()

    def home_notification(self):
        return self.content.get_public_notification()

    def site_media_asset(self, asset_id: int) -> ImageAsset:
        asset = self.db.query(ImageAsset).filter(ImageAsset.id == int(asset_id)).one_or_none()
        if asset is None:
            raise NotFoundError("Медиафайл не найден")
        scope = SiteContentService.ASSET_SCOPE
        asset_scope = str(getattr(asset, "scope", "") or "").strip()
        if asset_scope not in {"showcase", scope}:
            raise NotFoundError("Медиафайл не найден")
        return asset
