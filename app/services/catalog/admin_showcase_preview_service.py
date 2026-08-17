from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Designer, FilterAssignmentRuntimeState, ProductFilterAssignment
from app.services.catalog.admin_editor_service import AdminEditorService


CatalogViewKey = Literal["default", "designers", "sale"]
CategoryScope = Literal["new", "designers", "men", "women", "sale"]


@dataclass(slots=True)
class _CatalogHeaderCustomCatalogEntry:
    slug: str
    label: str
    description: str | None


@dataclass(slots=True)
class _CatalogHeaderMenuFilterEntry:
    id: str
    label: str
    section_values: list[str]


@dataclass(slots=True)
class _CatalogHeaderDesignerEntry:
    id: str
    label: str
    catalog_title: str
    catalog_description: str | None


class AdminShowcasePreviewService:
    _NON_RESTRICTIVE_QUERY_KEYS = {"sort", "ctx", "ctx_ref"}
    _TOP_MENU_DESIGNERS_LIMIT = 18
    _TOP_MENU_DESIGNERS_PER_COLUMN = 9
    _NEW_SECTION_FILTER_LIMIT = 9

    def __init__(self, db: Session) -> None:
        self.db = db
        self.editors = AdminEditorService(db)
        self._taxonomy_state: dict | None = None
        self._designer_state: dict | None = None
        self._designer_slugs_by_id: dict[str, str] | None = None
        self._filter_product_counts_by_slug_cache: dict[str, int] | None = None

    @staticmethod
    def _normalize_text(value: object | None) -> str:
        return " ".join(str(value or "").strip().split())

    @staticmethod
    def _normalize_slug(value: object | None) -> str:
        return AdminShowcasePreviewService._normalize_text(value).lower()

    @staticmethod
    def _int_or_zero(value: object | None) -> int:
        try:
            candidate = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return candidate

    @classmethod
    def _node_label(cls, node: dict | None) -> str:
        if not isinstance(node, dict):
            return ""
        return cls._normalize_text(node.get("label"))

    @classmethod
    def _node_display_label(cls, node: dict | None) -> str:
        if not isinstance(node, dict):
            return ""
        display_label = cls._normalize_text(node.get("display_label"))
        return display_label or cls._node_label(node)

    @staticmethod
    def _menu_filter_context_ref(attachment_id: object | None, filter_id: int) -> str:
        return f"{AdminShowcasePreviewService._normalize_text(attachment_id)}:{int(filter_id)}"

    @property
    def taxonomy_state(self) -> dict:
        if self._taxonomy_state is None:
            self._taxonomy_state = self.editors.list_taxonomy_editor_state()
        return self._taxonomy_state

    @property
    def designer_state(self) -> dict:
        if self._designer_state is None:
            self._designer_state = self.editors.list_designer_editor_state()
        return self._designer_state

    @property
    def designer_slugs_by_id(self) -> dict[str, str]:
        if self._designer_slugs_by_id is None:
            rows = self.db.query(Designer.id, Designer.slug).all()
            self._designer_slugs_by_id = {
                str(designer_id): str(slug or "").strip()
                for designer_id, slug in rows
                if str(designer_id or "").strip() and str(slug or "").strip()
            }
        return self._designer_slugs_by_id

    def _designer_slug_from_directory_item(self, item: dict) -> str:
        explicit_slug = str(item.get("slug") or "").strip()
        if explicit_slug:
            return explicit_slug
        return self.designer_slugs_by_id.get(str(item.get("id") or "").strip(), "")

    def _flatten_filters(self, nodes: list[dict]) -> list[dict]:
        result: list[dict] = []
        for node in nodes:
            result.append(node)
            children = node.get("children") if isinstance(node.get("children"), list) else []
            result.extend(self._flatten_filters(children))
        return result

    def _find_filter_by_id(self, filter_id: int) -> dict | None:
        target = int(filter_id or 0)
        if target <= 0:
            return None
        for node in self._flatten_filters(self.taxonomy_state.get("filters") or []):
            if self._int_or_zero(node.get("id")) == target:
                return node
        return None

    def _find_custom_catalog_by_id(self, catalog_id: int) -> dict | None:
        target = int(catalog_id or 0)
        if target <= 0:
            return None
        for catalog in self.taxonomy_state.get("custom_catalogs") or []:
            if self._int_or_zero(catalog.get("id")) == target:
                return catalog
        return None

    def _is_filter_visible(self, node: dict | None, hidden_node_ids: set[int]) -> bool:
        if not isinstance(node, dict):
            return False
        return bool(node.get("is_enabled")) and self._int_or_zero(node.get("id")) not in hidden_node_ids

    def _collect_visible_leaf_filters(self, nodes: list[dict], hidden_node_ids: set[int]) -> list[dict]:
        result: list[dict] = []
        for node in nodes:
            if not self._is_filter_visible(node, hidden_node_ids):
                continue
            children = node.get("children") if isinstance(node.get("children"), list) else []
            if not children:
                result.append(node)
                continue
            result.extend(self._collect_visible_leaf_filters(children, hidden_node_ids))
        return result

    def _collect_visible_branch_filters(self, root: dict, hidden_node_ids: set[int]) -> list[dict]:
        children = root.get("children") if isinstance(root.get("children"), list) else []
        direct_children = [node for node in children if self._is_filter_visible(node, hidden_node_ids)]
        if direct_children:
            return direct_children
        return self._collect_visible_leaf_filters([root], hidden_node_ids)

    def _visible_direct_children(self, node: dict, hidden_node_ids: set[int]) -> list[dict]:
        children = node.get("children") if isinstance(node.get("children"), list) else []
        return [child for child in children if self._is_filter_visible(child, hidden_node_ids)]

    def _build_menu_filter_item(
        self,
        *,
        node: dict,
        attachment_id: object | None,
        view_key: CatalogViewKey,
        gender: Literal["men", "women"] | None,
        hidden_node_ids: set[int],
    ) -> dict | None:
        section_values = [
            str(item.get("slug") or "")
            for item in self._collect_visible_leaf_filters([node], hidden_node_ids)
            if str(item.get("slug") or "").strip()
        ]
        if not section_values:
            return None
        return {
            "id": f"filter-item-{node['id']}",
            "kind": "filter_link",
            "label": self._node_label(node),
            "target": self._build_filter_target(
                view_key=view_key,
                gender=gender,  # type: ignore[arg-type]
                section_values=section_values,
                ctx="menu_filter",
                ctx_ref=self._menu_filter_context_ref(attachment_id, self._int_or_zero(node.get("id"))),
            ),
        }

    def _dedupe_by_slug(self, nodes: list[dict]) -> list[dict]:
        result: list[dict] = []
        seen: set[str] = set()
        for node in nodes:
            slug = self._normalize_slug(node.get("slug"))
            if not slug or slug in seen:
                continue
            seen.add(slug)
            result.append(node)
        return result

    @staticmethod
    def _category_matches_scope(category: dict, scope: CategoryScope) -> bool:
        behavior = str(category.get("behavior") or "").strip().lower()
        if scope == "new":
            return behavior == "new"
        if scope == "designers":
            return behavior == "designers"
        if scope == "sale":
            return behavior == "sale"
        return behavior == "gender" and str(category.get("system_filter_value") or "").strip().lower() == scope

    def _find_category_for_scope(self, scope: CategoryScope) -> dict | None:
        for category in self.taxonomy_state.get("categories") or []:
            if self._category_matches_scope(category, scope):
                return category
        return None

    @staticmethod
    def _build_filter_target(
        *,
        view_key: CatalogViewKey,
        section_values: list[str] | None = None,
        gender: Literal["men", "women"] | None = None,
        ctx: str | None = None,
        ctx_ref: str | None = None,
    ) -> dict:
        pathname = "/catalog/sale" if view_key == "sale" else "/catalog/designers" if view_key == "designers" else "/catalog"
        query: dict[str, str | list[str]] = {}
        if section_values:
            query["section"] = section_values
        if gender:
            query["gender"] = gender
        if ctx:
            query["ctx"] = ctx
        if ctx_ref:
            query["ctx_ref"] = ctx_ref
        return {"pathname": pathname, "query": query or None}

    @staticmethod
    def _read_query_values(search_params: dict[str, list[str]], key: str) -> list[str]:
        values = search_params.get(key) or []
        result: list[str] = []
        for value in values:
            result.extend([item.strip() for item in str(value or "").split(",") if item.strip()])
        return result

    @classmethod
    def _category_scope_from_view(cls, view_key: CatalogViewKey, search_params: dict[str, list[str]]) -> CategoryScope:
        if view_key == "sale":
            return "sale"
        gender = cls._normalize_slug((search_params.get("gender") or [None])[0])
        if gender in {"men", "women"}:
            return gender  # type: ignore[return-value]
        if view_key == "designers":
            return "designers"
        return "new"

    @classmethod
    def _letter_for_designer(cls, label: str) -> str:
        first = cls._normalize_text(label).upper()[:1]
        if first and "A" <= first <= "Z":
            return first
        return "#"

    def _build_designers_directory_entries(self) -> list[dict]:
        entries = []
        for item in self.taxonomy_state.get("designer_directory") or []:
            label = self._normalize_text(item.get("label"))
            slug = self._designer_slug_from_directory_item(item)
            if not label or not slug:
                continue
            entries.append(
                {
                    "id": str(item.get("id") or ""),
                    "slug": slug,
                    "label": label,
                    "letter": self._letter_for_designer(label),
                }
            )
        entries.sort(key=lambda item: (str(item["label"]).casefold(), str(item["slug"])))
        return entries

    def designers_directory(self) -> dict:
        entries = self._build_designers_directory_entries()
        available = {str(entry["letter"]) for entry in entries}
        alphabet = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        if "#" in available:
            alphabet.append("#")
        return {
            "alphabet": alphabet,
            "entries": entries,
        }

    def _build_designers_menu_blocks(self) -> list[dict]:
        featured = self._build_designer_options()[: self._TOP_MENU_DESIGNERS_LIMIT]
        blocks: list[dict] = []
        for column_index in range(0, len(featured), self._TOP_MENU_DESIGNERS_PER_COLUMN):
            column_items = featured[column_index : column_index + self._TOP_MENU_DESIGNERS_PER_COLUMN]
            if not column_items:
                continue
            blocks.append(
                {
                    "id": f"designers-col-{len(blocks) + 1}",
                    "title": None,
                    "items": [
                        {
                            "id": option["id"],
                            "kind": "system_link",
                            "label": option["label"],
                            "target": {
                                "pathname": "/catalog/designers",
                                "query": {
                                    "designer": option["value"],
                                    "ctx": "designer",
                                    "ctx_ref": option["value"],
                                },
                            },
                        }
                        for option in column_items
                    ],
                }
            )
        return blocks

    def _build_category_menu_blocks(self, scope: CategoryScope) -> list[dict]:
        category = self._find_category_for_scope(scope)
        if category is None:
            return []
        blocks: list[dict] = []
        view_key: CatalogViewKey = "sale" if scope == "sale" else "designers" if scope == "designers" else "default"
        gender = scope if scope in {"men", "women"} else None
        for attachment in category.get("attachments") or []:
            attachment_kind = str(attachment.get("kind") or "").strip()
            if attachment_kind == "custom_catalog":
                catalog = self._find_custom_catalog_by_id(self._int_or_zero(attachment.get("ref_id")))
                if catalog is None or not bool(catalog.get("is_enabled")):
                    continue
                blocks.append(
                    {
                        "id": f"catalog-{catalog['id']}",
                        "title": "Каталог",
                        "items": [
                            {
                                "id": f"catalog-item-{catalog['id']}",
                                "kind": "curated_listing",
                                "label": str(catalog.get("label") or ""),
                                "target": {
                                    "pathname": "/catalog/sale" if view_key == "sale" else "/catalog",
                                    "query": {
                                        "collection": str(catalog.get("slug") or ""),
                                        "ctx": "custom",
                                        "ctx_ref": str(catalog.get("slug") or ""),
                                        **({"gender": gender} if gender else {}),
                                    },
                                },
                            }
                        ],
                    }
                )
                continue
            root = self._find_filter_by_id(self._int_or_zero(attachment.get("ref_id")))
            if root is None or not bool(root.get("is_enabled")):
                continue
            hidden_node_ids = {
                self._int_or_zero(value)
                for value in (attachment.get("hidden_node_ids") or [])
                if self._int_or_zero(value) > 0
            }
            direct_children = [
                node
                for node in (root.get("children") if isinstance(root.get("children"), list) else [])
                if self._is_filter_visible(node, hidden_node_ids)
            ]
            nodes = self._dedupe_by_slug(self._collect_visible_branch_filters(root, hidden_node_ids))
            if not nodes:
                continue
            show_block_title = len(direct_children) > 0
            block_items: list[dict] = []
            block_groups: list[dict] = []
            for node in nodes:
                visible_children = self._visible_direct_children(node, hidden_node_ids)
                if visible_children:
                    group_items = [
                        item
                        for child in self._dedupe_by_slug(self._collect_visible_leaf_filters(visible_children, hidden_node_ids))
                        if (item := self._build_menu_filter_item(
                            node=child,
                            attachment_id=attachment.get("id"),
                            view_key=view_key,
                            gender=gender,  # type: ignore[arg-type]
                            hidden_node_ids=hidden_node_ids,
                        ))
                        is not None
                    ]
                    if group_items:
                        block_groups.append(
                            {
                                "id": f"filter-group-{node['id']}",
                                "title": self._node_label(node),
                                "titleTarget": self._build_filter_target(
                                    view_key=view_key,
                                    gender=gender,  # type: ignore[arg-type]
                                    section_values=[
                                        str(item.get("slug") or "")
                                        for item in self._collect_visible_leaf_filters([node], hidden_node_ids)
                                        if str(item.get("slug") or "").strip()
                                    ],
                                    ctx="menu_filter",
                                    ctx_ref=self._menu_filter_context_ref(
                                        attachment.get("id"),
                                        self._int_or_zero(node.get("id")),
                                    ),
                                ),
                                "items": group_items,
                            }
                        )
                    continue
                item = self._build_menu_filter_item(
                    node=node,
                    attachment_id=attachment.get("id"),
                    view_key=view_key,
                    gender=gender,  # type: ignore[arg-type]
                    hidden_node_ids=hidden_node_ids,
                )
                if item is not None:
                    block_items.append(item)
            blocks.append(
                {
                    "id": f"filter-{root['id']}",
                    "title": self._node_label(root) if show_block_title else None,
                    "titleTarget": (
                        self._build_filter_target(
                            view_key=view_key,
                            gender=gender,  # type: ignore[arg-type]
                            section_values=[
                                str(item.get("slug") or "")
                                for item in self._collect_visible_leaf_filters([root], hidden_node_ids)
                                if str(item.get("slug") or "").strip()
                            ],
                            ctx="menu_filter",
                            ctx_ref=self._menu_filter_context_ref(
                                attachment.get("id"),
                                self._int_or_zero(root.get("id")),
                            ),
                        )
                        if show_block_title
                        else None
                    ),
                    "items": block_items,
                    "groups": block_groups,
                }
            )
        return blocks

    def navigation(self) -> dict:
        return {
            "sections": [
                {
                    "key": "new",
                    "label": "НОВИНКИ",
                    "target": None,
                    "menu": {
                        "id": "new-menu",
                        "layout": "new",
                        "blocks": self._build_new_menu_blocks(),
                    },
                },
                {
                    "key": "designers",
                    "label": "ДИЗАЙНЕРЫ",
                    "target": {"pathname": "/catalog/designers", "query": None},
                    "menu": {
                        "id": "designers-menu",
                        "layout": "designers",
                        "blocks": self._build_designers_menu_blocks(),
                        "footerLink": {
                            "label": "Смотреть все",
                            "target": {"pathname": "/designers", "query": None},
                        },
                    },
                },
                {
                    "key": "men",
                    "label": "МУЖСКОЕ",
                    "target": {"pathname": "/catalog", "query": {"gender": "men"}},
                    "menu": {
                        "id": "men-menu",
                        "layout": "category_columns",
                        "blocks": self._build_category_menu_blocks("men"),
                    },
                },
                {
                    "key": "women",
                    "label": "ЖЕНСКОЕ",
                    "target": {"pathname": "/catalog", "query": {"gender": "women"}},
                    "menu": {
                        "id": "women-menu",
                        "layout": "category_columns",
                        "blocks": self._build_category_menu_blocks("women"),
                    },
                },
                {
                    "key": "sale",
                    "label": "СКИДКИ",
                    "target": {"pathname": "/catalog/sale", "query": {"ctx": "sale"}},
                    "menu": None,
                },
            ]
        }

    def _build_designer_options(self) -> list[dict]:
        items = []
        for item in self.taxonomy_state.get("designer_directory") or []:
            label = self._normalize_text(item.get("label"))
            slug = self._designer_slug_from_directory_item(item)
            if not label or not slug:
                continue
            items.append(
                {
                    "id": slug,
                    "label": label,
                    "value": slug,
                    "product_count": self._int_or_zero(item.get("product_count")),
                }
            )
        items.sort(key=lambda item: (-int(item["product_count"]), str(item["label"]).casefold(), str(item["value"])))
        return items

    def _filter_product_counts_by_slug(self) -> dict[str, int]:
        if self._filter_product_counts_by_slug_cache is not None:
            return dict(self._filter_product_counts_by_slug_cache)
        state = (
            self.db.query(FilterAssignmentRuntimeState)
            .filter(FilterAssignmentRuntimeState.id == 1)
            .one_or_none()
        )
        revision = int(getattr(state, "applied_revision", 0) or 0)
        if revision <= 0:
            self._filter_product_counts_by_slug_cache = {}
            return {}
        self._filter_product_counts_by_slug_cache = {
            str(slug): int(count)
            for slug, count in (
                self.db.query(
                    ProductFilterAssignment.filter_slug,
                    func.count(func.distinct(ProductFilterAssignment.product_id)).label("product_count"),
                )
                .filter(ProductFilterAssignment.revision == revision)
                .group_by(ProductFilterAssignment.filter_slug)
                .all()
            )
            if str(slug or "").strip()
        }
        return dict(self._filter_product_counts_by_slug_cache)

    def _top_new_section_filter_items(self) -> list[dict]:
        counts_by_slug = self._filter_product_counts_by_slug()
        leaf_filters = [
            node
            for node in self._collect_visible_leaf_filters(self.taxonomy_state.get("filters") or [], set())
            if self._normalize_slug(node.get("slug"))
        ]
        leaf_filters.sort(
            key=lambda node: (
                -int(counts_by_slug.get(self._normalize_slug(node.get("slug")), 0)),
                self._node_label(node).casefold(),
                self._normalize_slug(node.get("slug")),
            )
        )
        items: list[dict] = []
        for node in leaf_filters[: self._NEW_SECTION_FILTER_LIMIT]:
            slug = self._normalize_slug(node.get("slug"))
            if not slug:
                continue
            items.append(
                {
                    "id": f"new-section-{slug}",
                    "kind": "filter_link",
                    "label": self._node_label(node),
                    "target": {
                        "pathname": "/catalog",
                        "query": {
                            "section": [slug],
                            "ctx": "menu_filter",
                            "ctx_ref": f"new-section:{slug}",
                        },
                    },
                }
            )
        return items

    def _new_collection_items(self) -> list[dict]:
        items = [
            {
                "id": "new-availability-in-stock",
                "kind": "system_link",
                "label": "В наличии",
                "target": {"pathname": "/catalog", "query": {"availability": "in-stock"}},
            },
            {
                "id": "new-availability-preorder",
                "kind": "system_link",
                "label": "Под заказ",
                "target": {"pathname": "/catalog", "query": {"availability": "preorder"}},
            },
        ]
        new_category = self._find_category_for_scope("new")
        if new_category is not None:
            for attachment in new_category.get("attachments") or []:
                if str(attachment.get("kind") or "").strip() != "custom_catalog":
                    continue
                catalog = self._find_custom_catalog_by_id(self._int_or_zero(attachment.get("ref_id")))
                if catalog is None or not bool(catalog.get("is_enabled")):
                    continue
                items.append(
                    {
                        "id": f"new-catalog-{catalog['id']}",
                        "kind": "curated_listing",
                        "label": str(catalog.get("label") or ""),
                        "target": {
                            "pathname": "/catalog",
                            "query": {
                                "collection": str(catalog.get("slug") or ""),
                                "ctx": "custom",
                                "ctx_ref": str(catalog.get("slug") or ""),
                            },
                        },
                    }
                )
        items.append(
            {
                "id": "new-all-products",
                "kind": "system_link",
                "label": "Все товары",
                "target": {"pathname": "/catalog", "query": None},
            }
        )
        return items

    def _build_new_menu_blocks(self) -> list[dict]:
        return [
            {
                "id": "new-availability",
                "title": "Коллекции",
                "items": self._new_collection_items(),
            },
            {
                "id": "new-sections",
                "title": "Разделы",
                "items": self._top_new_section_filter_items(),
            },
        ]

    def _collect_section_filter_options_for_scope(self, scope: CategoryScope) -> list[dict]:
        category = self._find_category_for_scope(scope)
        if category is None:
            return []
        options: list[dict] = []
        seen: set[str] = set()
        for attachment in category.get("attachments") or []:
            if str(attachment.get("kind") or "").strip() != "filter":
                continue
            root = self._find_filter_by_id(self._int_or_zero(attachment.get("ref_id")))
            if root is None or not bool(root.get("is_enabled")):
                continue
            hidden_node_ids = {
                self._int_or_zero(value)
                for value in (attachment.get("hidden_node_ids") or [])
                if self._int_or_zero(value) > 0
            }
            for node in self._collect_visible_leaf_filters([root], hidden_node_ids):
                slug = self._normalize_slug(node.get("slug"))
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                options.append(
                    {
                        "value": slug,
                        "label": self._node_display_label(node),
                    }
                )
        return options

    def _collect_all_section_filter_options(self) -> list[dict]:
        options: list[dict] = []
        seen: set[str] = set()
        for node in self._collect_visible_leaf_filters(self.taxonomy_state.get("filters") or [], set()):
            slug = self._normalize_slug(node.get("slug"))
            if not slug or slug in seen:
                continue
            seen.add(slug)
            options.append(
                {
                    "value": slug,
                    "label": self._node_display_label(node),
                }
            )
        return options

    def _build_section_filter_options(self, scope: CategoryScope) -> list[dict]:
        return self._collect_all_section_filter_options()

    def _build_catalog_filter_groups(self, view_key: CatalogViewKey, search_params: dict[str, list[str]]) -> list[dict]:
        scope = self._category_scope_from_view(view_key, search_params)
        groups: list[dict] = []
        groups.append(
            {
                "key": "sort",
                "label": "СОРТИРОВКА",
                "queryParam": "sort",
                "selectionMode": "single",
                "options": [
                    {"id": "sort-featured", "label": "Сначала новые", "value": "featured"},
                    {"id": "sort-price-asc", "label": "Сначала дешевле", "value": "price_asc"},
                    {"id": "sort-price-desc", "label": "Сначала дороже", "value": "price_desc"},
                ],
            }
        )
        groups.append(
            {
                "key": "availability",
                "label": "НАЛИЧИЕ",
                "queryParam": "availability",
                "selectionMode": "single",
                "options": [
                    {"id": "availability-in-stock", "label": "В наличии", "value": "in_stock"},
                    {"id": "availability-by-order", "label": "Под заказ", "value": "by_order"},
                ],
            }
        )
        section_options = self._build_section_filter_options(scope)
        if section_options:
            groups.append(
                {
                    "key": "section",
                    "label": "РАЗДЕЛ",
                    "queryParam": "section",
                    "selectionMode": "multiple",
                    "options": [
                        {
                            "id": option["value"],
                            "label": option["label"],
                            "value": option["value"],
                        }
                        for option in section_options
                    ],
                    "visibleOptionsLimit": 9,
                    "panelWidth": "wide",
                    "maxVisibleOptions": 12,
                    "prioritizeSelected": True,
                }
            )
        designer_options = self._build_designer_options()
        if designer_options:
            groups.append(
                {
                    "key": "designer",
                    "label": "ДИЗАЙНЕРЫ",
                    "queryParam": "designer",
                    "selectionMode": "multiple",
                    "options": [
                        {
                            "id": option["id"],
                            "label": option["label"],
                            "value": option["value"],
                        }
                        for option in designer_options
                    ],
                    "visibleOptionsLimit": 7,
                    "panelWidth": "wide",
                    "prioritizeSelected": True,
                    "actionItem": {
                        "label": "Смотреть все",
                        "target": {"pathname": "/designers", "query": None},
                        "carryKeys": ["designer"],
                        "emphasis": "strong",
                    },
                }
            )
        groups.append(
            {
                "key": "gender",
                "label": "ПОЛ",
                "queryParam": "gender",
                "selectionMode": "single",
                "options": [
                    {"id": "men", "label": "Мужское", "value": "men"},
                    {"id": "women", "label": "Женское", "value": "women"},
                ],
            }
        )
        return groups

    def _build_preview_metrics(self, view_key: CatalogViewKey) -> list[dict]:
        enabled_leaf_filters = len(self._dedupe_by_slug(self._collect_visible_leaf_filters(self.taxonomy_state.get("filters") or [], set())))
        visible_catalogs = len([item for item in self.taxonomy_state.get("custom_catalogs") or [] if bool(item.get("is_enabled"))])
        designers = len([item for item in self.taxonomy_state.get("designer_directory") or [] if self._normalize_text(item.get("label"))])
        return [
            {"id": f"{view_key}-filters", "label": "Фильтры", "value": str(enabled_leaf_filters)},
            {"id": f"{view_key}-catalogs", "label": "Кастомные каталоги", "value": str(visible_catalogs)},
            {"id": f"{view_key}-designers", "label": "Дизайнеры", "value": str(designers)},
        ]

    def _build_catalog_header_custom_catalogs(self) -> list[_CatalogHeaderCustomCatalogEntry]:
        result: list[_CatalogHeaderCustomCatalogEntry] = []
        for catalog in self.taxonomy_state.get("custom_catalogs") or []:
            if not bool(catalog.get("is_enabled")):
                continue
            label = self._normalize_text(catalog.get("label"))
            slug = str(catalog.get("slug") or "").strip()
            if not label or not slug:
                continue
            description = self._normalize_text(catalog.get("description")) or None
            result.append(
                _CatalogHeaderCustomCatalogEntry(
                    slug=slug,
                    label=label,
                    description=description,
                )
            )
        return result

    def _build_catalog_header_menu_filters(self) -> list[_CatalogHeaderMenuFilterEntry]:
        items: list[_CatalogHeaderMenuFilterEntry] = []
        for node in self._collect_visible_leaf_filters(self.taxonomy_state.get("filters") or [], set()):
            slug = str(node.get("slug") or "").strip()
            if not slug:
                continue
            items.append(
                _CatalogHeaderMenuFilterEntry(
                    id=f"new-section:{slug}",
                    label=self._node_label(node),
                    section_values=[slug],
                )
            )
        for category in self.taxonomy_state.get("categories") or []:
            if str(category.get("behavior") or "").strip() != "gender":
                continue
            for attachment in category.get("attachments") or []:
                if str(attachment.get("kind") or "").strip() != "filter":
                    continue
                root = self._find_filter_by_id(self._int_or_zero(attachment.get("ref_id")))
                if root is None or not bool(root.get("is_enabled")):
                    continue
                hidden_node_ids = {
                    self._int_or_zero(value)
                    for value in (attachment.get("hidden_node_ids") or [])
                    if self._int_or_zero(value) > 0
                }
                root_section_values = [
                    str(item.get("slug") or "")
                    for item in self._collect_visible_leaf_filters([root], hidden_node_ids)
                    if str(item.get("slug") or "").strip()
                ]
                if root_section_values:
                    items.append(
                        _CatalogHeaderMenuFilterEntry(
                            id=self._menu_filter_context_ref(attachment.get("id"), self._int_or_zero(root.get("id"))),
                            label=self._node_label(root),
                            section_values=root_section_values,
                        )
                    )
                for node in self._collect_visible_branch_filters(root, hidden_node_ids):
                    section_values = [
                        str(item.get("slug") or "")
                        for item in self._collect_visible_leaf_filters([node], hidden_node_ids)
                        if str(item.get("slug") or "").strip()
                    ]
                    if not section_values:
                        continue
                    items.append(
                        _CatalogHeaderMenuFilterEntry(
                            id=self._menu_filter_context_ref(attachment.get("id"), self._int_or_zero(node.get("id"))),
                            label=self._node_label(node),
                            section_values=section_values,
                        )
                    )
                    slug = str(node.get("slug") or "").strip()
                    if slug:
                        items.append(
                            _CatalogHeaderMenuFilterEntry(
                                id=f"mobile:{slug}",
                                label=self._node_label(node),
                                section_values=section_values,
                            )
                        )
        merged: dict[str, _CatalogHeaderMenuFilterEntry] = {}
        for item in items:
            current = merged.get(item.id)
            if current is None:
                merged[item.id] = item
                continue
            values = list(current.section_values)
            seen = set(values)
            for value in item.section_values:
                if value not in seen:
                    seen.add(value)
                    values.append(value)
            merged[item.id] = _CatalogHeaderMenuFilterEntry(
                id=current.id,
                label=current.label,
                section_values=values,
            )
        return list(merged.values())

    def _build_catalog_header_designers(self) -> list[_CatalogHeaderDesignerEntry]:
        descriptions_by_id = {
            str(item.get("id") or ""): (str(item.get("description") or "").strip() or None)
            for item in self.designer_state.get("designers") or []
            if str(item.get("id") or "").strip()
        }
        result: list[_CatalogHeaderDesignerEntry] = []
        for item in self.taxonomy_state.get("designer_directory") or []:
            label = self._normalize_text(item.get("label"))
            designer_id = str(item.get("id") or "").strip()
            slug = self._designer_slug_from_directory_item(item)
            if not label or not designer_id or not slug:
                continue
            result.append(
                _CatalogHeaderDesignerEntry(
                    id=slug,
                    label=label,
                    catalog_title=label,
                    catalog_description=descriptions_by_id.get(designer_id),
                )
            )
        result.sort(key=lambda item: (item.label.casefold(), item.id))
        return result

    @classmethod
    def _read_context(cls, search_params: dict[str, list[str]]) -> str | None:
        value = cls._normalize_text((search_params.get("ctx") or [None])[0]).lower()
        return value if value in {"all", "custom", "designer", "menu_filter", "sale"} else None

    @classmethod
    def _has_restrictive_filters(cls, search_params: dict[str, list[str]]) -> bool:
        for key, values in search_params.items():
            if key in cls._NON_RESTRICTIVE_QUERY_KEYS:
                continue
            if any(cls._normalize_text(value) for value in values):
                return True
        return False

    @staticmethod
    def _has_same_selection(left: list[str], right: list[str]) -> bool:
        return len(left) == len(right) and set(left) == set(right)

    @staticmethod
    def _designer_header(designer: _CatalogHeaderDesignerEntry | None) -> dict:
        return {
            "title": designer.catalog_title if designer is not None else "Дизайнер",
            "description": designer.catalog_description if designer is not None else None,
            "source": "designer",
        }

    @classmethod
    def _search_header(cls, search_params: dict[str, list[str]]) -> dict | None:
        query = cls._normalize_text((search_params.get("q") or [None])[0])
        if not query:
            return None
        return {"title": f"Поиск: {query}", "description": None, "source": "search"}

    def _resolve_catalog_page_header(self, *, view_key: CatalogViewKey, search_params: dict[str, list[str]]) -> dict:
        search_header = self._search_header(search_params)
        if search_header is not None:
            return search_header
        context = self._read_context(search_params)
        context_ref = self._normalize_text((search_params.get("ctx_ref") or [None])[0])
        selected_designers = self._read_query_values(search_params, "designer")
        selected_sections = self._read_query_values(search_params, "section")
        custom_catalogs = self._build_catalog_header_custom_catalogs()
        menu_filters = self._build_catalog_header_menu_filters()
        designers = self._build_catalog_header_designers()
        if view_key == "sale" or context == "sale":
            return {"title": "Скидки", "description": None, "source": "sale"}
        if context == "custom" and context_ref:
            custom_catalog = next((item for item in custom_catalogs if item.slug == context_ref), None)
            if custom_catalog is not None:
                return {
                    "title": custom_catalog.label,
                    "description": custom_catalog.description,
                    "source": "custom_catalog",
                }
        if context == "menu_filter" and context_ref:
            menu_filter = next((item for item in menu_filters if item.id == context_ref), None)
            if menu_filter is not None and self._has_same_selection(selected_sections, menu_filter.section_values):
                return {
                    "title": menu_filter.label,
                    "description": None,
                    "source": "menu_filter",
                }
        if len(selected_designers) == 1:
            designer = next((item for item in designers if item.id == selected_designers[0]), None)
            return self._designer_header(designer)
        if view_key == "designers" or len(selected_designers) > 1:
            return {"title": "Дизайнеры", "description": None, "source": "multiple_designers"}
        if not self._has_restrictive_filters(search_params):
            return {"title": "Все товары", "description": None, "source": "all_products"}
        return {"title": "Каталог", "description": None, "source": "catalog"}

    def catalog_experience(self, *, view_key: CatalogViewKey, search_params: dict[str, list[str]]) -> dict:
        return {
            "view": {
                "key": view_key,
                "header": self._resolve_catalog_page_header(view_key=view_key, search_params=search_params),
                "globalConstraints": ["Только товары с активной скидкой"] if view_key == "sale" else None,
            },
            "filterGroups": self._build_catalog_filter_groups(view_key, search_params),
            "previewMetrics": self._build_preview_metrics(view_key),
        }
