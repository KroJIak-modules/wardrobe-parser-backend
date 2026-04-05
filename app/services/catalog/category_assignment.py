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
    is_favorite: bool


class CategoryAssigner:
    def __init__(self, tree: list[CategoryTreeNodeResponse]):
        self._tree = tree
        self._flat = self._flatten(tree)
        self._fallback = next((node for node in self._flat if node.is_fallback), None)
        self._favorite = next((node for node in self._flat if getattr(node, "is_favorite", False)), None)

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

    def _haystack(self, item: Any) -> str:
        if isinstance(item, dict):
            title = self._as_text(item.get("title"))
            vendor = self._as_text(item.get("vendor"))
            product_type = self._as_text(item.get("product_type"))
            handle = self._as_text(item.get("handle"))
        else:
            title = self._as_text(getattr(item, "title", None))
            vendor = self._as_text(getattr(item, "vendor", None))
            product_type = self._as_text(getattr(item, "product_type", None))
            handle = self._as_text(getattr(item, "handle", None))
        return " ".join([title, vendor, product_type, handle]).lower()

    def match(self, item: Any, *, is_favorite: bool = False) -> CategoryMatch:
        if is_favorite and self._favorite is not None:
            return CategoryMatch(
                category_id=self._favorite.id,
                category_name=self._favorite.name,
                category_slug=self._favorite.slug,
                is_fallback=False,
                is_favorite=True,
            )

        haystack = self._haystack(item)
        best_node: CategoryTreeNodeResponse | None = None
        best_score = 0

        for node in self._flat:
            if node.is_fallback or getattr(node, "is_favorite", False):
                continue
            if not node.effective_keywords:
                continue
            score = 0
            for keyword in node.effective_keywords:
                normalized = keyword.strip().lower()
                if not normalized:
                    continue
                if normalized in haystack:
                    score += len(normalized)
            if score > 0 and score > best_score:
                best_node = node
                best_score = score

        chosen = best_node or self._fallback
        if chosen is None:
            return CategoryMatch(
                category_id=0,
                category_name="Прочее",
                category_slug="prochee",
                is_fallback=True,
                is_favorite=False,
            )

        return CategoryMatch(
            category_id=chosen.id,
            category_name=chosen.name,
            category_slug=chosen.slug,
            is_fallback=bool(chosen.is_fallback),
            is_favorite=bool(getattr(chosen, "is_favorite", False)),
        )

    def direct_counts(self, items: list[Any], favorite_product_ids: set[int] | None = None) -> dict[int, int]:
        direct: dict[int, int] = {}
        favorite_set = favorite_product_ids or set()
        for item in items:
            if isinstance(item, dict):
                product_id_raw = item.get("id")
            else:
                product_id_raw = getattr(item, "id", None)
            try:
                product_id = int(product_id_raw) if product_id_raw is not None else None
            except (TypeError, ValueError):
                product_id = None
            is_favorite = product_id is not None and product_id in favorite_set
            matched = self.match(item, is_favorite=is_favorite)
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
