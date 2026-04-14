"""Tree-building helpers for category services."""

from __future__ import annotations

from app.models import ParserCategory
from app.repositories import ParserCategoryKeywordRepository
from app.schemas.parser import CategoryTreeNodeResponse


def build_tree(
    categories: list[ParserCategory],
    keyword_repo: ParserCategoryKeywordRepository,
    product_counts: dict[int, int] | None = None,
    designers_root_id: int | None = None,
) -> list[CategoryTreeNodeResponse]:
    by_parent: dict[int | None, list[ParserCategory]] = {}
    for category in categories:
        by_parent.setdefault(category.parent_id, []).append(category)

    for nodes in by_parent.values():
        nodes.sort(key=lambda c: c.id)

    keyword_map = keyword_repo.get_grouped_keywords(scope="local")
    title_keyword_map = keyword_repo.get_grouped_keywords(scope="title")

    def walk(node: ParserCategory, in_designers_branch: bool) -> CategoryTreeNodeResponse:
        own_keywords = [] if node.is_fallback or bool(getattr(node, "is_favorite", False)) else list(keyword_map.get(int(node.id), []))
        own_title_keywords = [] if node.is_fallback or bool(getattr(node, "is_favorite", False)) else list(title_keyword_map.get(int(node.id), []))
        raw_children = by_parent.get(node.id, [])
        next_in_designers = in_designers_branch or (designers_root_id is not None and int(node.id) == int(designers_root_id))
        children = [walk(child, next_in_designers) for child in raw_children]
        is_system = bool(node.is_fallback) or bool(getattr(node, "is_favorite", False)) or next_in_designers
        has_children = len(raw_children) > 0
        keywords_editable = (not is_system) and (not has_children)
        if not keywords_editable:
            if next_in_designers:
                locked_reason = "В ветке «Дизайнеры» ключевые слова отключены и поддерживаются автоматически."
            elif is_system:
                locked_reason = "У системной категории ключевые слова недоступны."
            else:
                locked_reason = "Ключевые слова доступны только у конечных (листовых) категорий."
        else:
            locked_reason = None
        return CategoryTreeNodeResponse(
            id=node.id,
            name=node.name,
            slug=node.slug,
            parent_id=node.parent_id,
            is_fallback=node.is_fallback,
            is_favorite=bool(getattr(node, "is_favorite", False)),
            is_enabled=bool(getattr(node, "is_enabled", True)),
            is_system=is_system,
            has_children=has_children,
            keywords_editable=keywords_editable,
            keywords_locked_reason=locked_reason,
            is_designers_root=bool(designers_root_id is not None and int(node.id) == int(designers_root_id)),
            is_in_designers_branch=next_in_designers,
            product_count=(product_counts or {}).get(node.id, 0),
            keywords=own_keywords,
            title_keywords=own_title_keywords,
            effective_keywords=own_keywords,
            children=children,
        )

    roots = by_parent.get(None, [])
    return [walk(root, False) for root in roots]


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
    own_keywords = [item.keyword for item in keyword_repo.get_by_category(category.id, scope="local")]
    own_title_keywords = [item.keyword for item in keyword_repo.get_by_category(category.id, scope="title")]
    return CategoryTreeNodeResponse(
        id=category.id,
        name=category.name,
        slug=category.slug,
        parent_id=category.parent_id,
        is_fallback=category.is_fallback,
        is_favorite=bool(getattr(category, "is_favorite", False)),
        is_enabled=bool(getattr(category, "is_enabled", True)),
        is_system=bool(category.is_fallback) or bool(getattr(category, "is_favorite", False)),
        has_children=False,
        keywords_editable=not (bool(category.is_fallback) or bool(getattr(category, "is_favorite", False))),
        keywords_locked_reason=None,
        is_designers_root=False,
        is_in_designers_branch=False,
        product_count=0,
        keywords=own_keywords,
        title_keywords=own_title_keywords,
        effective_keywords=own_keywords,
        children=[],
    )
