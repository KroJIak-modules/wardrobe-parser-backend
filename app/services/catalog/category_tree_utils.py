"""Tree-building helpers for category services."""

from __future__ import annotations

from app.models import ParserCategory
from app.repositories import ParserCategoryKeywordRepository
from app.schemas.parser import CategoryTreeNodeResponse


def build_tree(categories: list[ParserCategory], keyword_repo: ParserCategoryKeywordRepository) -> list[CategoryTreeNodeResponse]:
    by_parent: dict[int | None, list[ParserCategory]] = {}
    for category in categories:
        by_parent.setdefault(category.parent_id, []).append(category)

    for nodes in by_parent.values():
        nodes.sort(key=lambda c: (not c.is_fallback, c.name.lower()))

    def walk(node: ParserCategory, inherited: list[str]) -> CategoryTreeNodeResponse:
        own_keywords = [] if node.is_fallback else [item.keyword for item in keyword_repo.get_by_category(node.id)]
        effective = sorted(set([*inherited, *own_keywords]))
        children = [walk(child, effective) for child in by_parent.get(node.id, [])]
        return CategoryTreeNodeResponse(
            id=node.id,
            name=node.name,
            slug=node.slug,
            parent_id=node.parent_id,
            is_fallback=node.is_fallback,
            keywords=own_keywords,
            effective_keywords=effective,
            children=children,
        )

    roots = by_parent.get(None, [])
    return [walk(root, []) for root in roots]


def is_descendant(categories: list[ParserCategory], ancestor_id: int, candidate_id: int) -> bool:
    by_parent: dict[int | None, list[int]] = {}
    for item in categories:
        by_parent.setdefault(item.parent_id, []).append(item.id)

    stack = list(by_parent.get(ancestor_id, []))
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if current == candidate_id:
            return True
        stack.extend(by_parent.get(current, []))
    return False


def find_node(tree: list[CategoryTreeNodeResponse], category_id: int) -> CategoryTreeNodeResponse | None:
    for node in tree:
        if node.id == category_id:
            return node
        found = find_node(node.children, category_id)
        if found:
            return found
    return None


def build_single_node_response(
    category: ParserCategory,
    keyword_repo: ParserCategoryKeywordRepository,
) -> CategoryTreeNodeResponse:
    own_keywords = [item.keyword for item in keyword_repo.get_by_category(category.id)]
    return CategoryTreeNodeResponse(
        id=category.id,
        name=category.name,
        slug=category.slug,
        parent_id=category.parent_id,
        is_fallback=category.is_fallback,
        keywords=own_keywords,
        effective_keywords=own_keywords,
        children=[],
    )
