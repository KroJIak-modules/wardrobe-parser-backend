"""Category assignment helpers shared across API endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas.parser import CategoryTreeNodeResponse


@dataclass
class CategoryMatch:
    category_id: int
    category_name: str
    category_slug: str
    is_fallback: bool


class CategoryAssigner:
    def __init__(self, tree: list[CategoryTreeNodeResponse], manual_category_ids_by_product: dict[int, list[int]] | None = None):
        self._tree = tree
        self._flat = self._flatten(tree)
        self._fallback = next((node for node in self._flat if node.is_fallback), None)
        self._by_id = {int(node.id): node for node in self._flat}
        self._manual_category_ids_by_product = manual_category_ids_by_product or {}

    @staticmethod
    def _flatten(nodes: list[CategoryTreeNodeResponse]) -> list[CategoryTreeNodeResponse]:
        result: list[CategoryTreeNodeResponse] = []
        for node in nodes:
            result.append(node)
            result.extend(CategoryAssigner._flatten(node.children))
        return result

    @staticmethod
    def _as_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _local_haystack(self, item: Any) -> str:
        if isinstance(item, dict):
            vendor = self._as_text(item.get("vendor"))
            product_type = self._as_text(item.get("product_type"))
        else:
            vendor = self._as_text(getattr(item, "vendor", None))
            product_type = self._as_text(getattr(item, "product_type", None))
        return " ".join([vendor, product_type]).lower()

    def _title_haystack(self, item: Any) -> str:
        if isinstance(item, dict):
            title = self._as_text(item.get("title"))
        else:
            title = self._as_text(getattr(item, "title", None))
        return title.lower()

    @staticmethod
    def _to_category_match(node: CategoryTreeNodeResponse) -> CategoryMatch:
        return CategoryMatch(
            category_id=node.id,
            category_name=node.name,
            category_slug=node.slug,
            is_fallback=bool(node.is_fallback),
        )

    def match_many(self, item: Any) -> list[CategoryMatch]:

        if isinstance(item, dict):
            product_id_raw = item.get("id")
        else:
            product_id_raw = getattr(item, "id", None)
        try:
            product_id = int(product_id_raw) if product_id_raw is not None else None
        except (TypeError, ValueError):
            product_id = None

        local_haystack = self._local_haystack(item)
        title_haystack = self._title_haystack(item)
        matched_nodes: list[tuple[int, int, CategoryTreeNodeResponse]] = []

        for node in self._flat:
            if node.is_fallback:
                continue
            if not bool(getattr(node, "is_enabled", True)):
                continue
            local_keywords = list(getattr(node, "keywords", []) or [])
            title_keywords = list(getattr(node, "title_keywords", []) or [])
            if not local_keywords and not title_keywords:
                continue
            score = 0
            for keyword in local_keywords:
                normalized = keyword.strip().lower()
                if not normalized:
                    continue
                if normalized in local_haystack:
                    score += len(normalized)
            for keyword in title_keywords:
                normalized = keyword.strip().lower()
                if not normalized:
                    continue
                if normalized in title_haystack:
                    score += len(normalized)
            if score > 0:
                matched_nodes.append((-score, int(node.id), node))

        matched_nodes.sort(key=lambda item: (item[0], item[1]))
        auto_matches = [self._to_category_match(node) for _, _, node in matched_nodes]
        manual_matches: list[CategoryMatch] = []
        if product_id is not None:
            for category_id in self._manual_category_ids_by_product.get(product_id, []):
                node = self._by_id.get(int(category_id))
                if node is None:
                    continue
                if not bool(getattr(node, "is_enabled", True)):
                    continue
                manual_matches.append(self._to_category_match(node))

        ordered: list[CategoryMatch] = []
        seen: set[int] = set()
        for match in [*manual_matches, *auto_matches]:
            if match.category_id <= 0 or match.category_id in seen:
                continue
            seen.add(match.category_id)
            ordered.append(match)

        if not ordered:
            if self._fallback is None:
                return [
                    CategoryMatch(
                        category_id=0,
                        category_name="Прочее",
                        category_slug="prochee",
                        is_fallback=True,
                    )
                ]
            return [self._to_category_match(self._fallback)]
        return ordered

    def match(self, item: Any) -> CategoryMatch:
        return self.match_many(item)[0]

    def direct_counts(self, items: list[Any]) -> dict[int, int]:
        direct: dict[int, int] = {}
        for item in items:
            matched_categories = self.match_many(item)
            for matched in matched_categories:
                if matched.category_id <= 0:
                    continue
                direct[matched.category_id] = direct.get(matched.category_id, 0) + 1
        return direct


def aggregate_tree_counts(nodes: list[CategoryTreeNodeResponse], direct_counts: dict[int, int]) -> dict[int, int]:
    aggregated: dict[int, int] = {}

    def walk(node: CategoryTreeNodeResponse) -> int:
        own = direct_counts.get(node.id, 0)
        child_total = 0
        for child in node.children:
            child_total += walk(child)
        total = own + child_total
        aggregated[node.id] = total
        return total

    for root in nodes:
        walk(root)
    return aggregated
