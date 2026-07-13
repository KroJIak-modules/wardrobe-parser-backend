from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import FilterAssignmentRuntimeState, Product, ProductFilterAssignment
from app.repositories.catalog_filter_assignments import CatalogFilterAssignmentRepository
from app.repositories.catalog_products import CatalogProductRepository
from app.repositories.catalog_taxonomy import CatalogTaxonomyRepository
from app.services.catalog.filter_assignment_queue import ProductFilterAssignmentQueue
from app.services.settings.weight_rule_matcher import keyword_matches, keyword_pattern, normalize_haystack, normalize_keyword


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _FilterSpec:
    id: int
    slug: str
    label: str
    local_keywords: list[str]
    title_keywords: list[str]
    manual_product_ids: set[int]
    restrict_by_gender: bool
    allowed_genders: set[str] | None


@dataclass(slots=True)
class _AssignmentResult:
    product_id: int
    filter_slug: str
    filter_label: str
    manual_rank: int
    match_score: int
    title_tail_distance: int | None
    matched_local_keywords: list[str]
    matched_title_keywords: list[str]


class ProductFilterAssignmentService:
    _LOCAL_KEYWORD_SCORE = 1
    _TITLE_KEYWORD_SCORE = 2

    def __init__(self, db: Session) -> None:
        self.db = db
        self.assignments = CatalogFilterAssignmentRepository(db)
        self.products = CatalogProductRepository(db)
        self.taxonomy = CatalogTaxonomyRepository(db)
        self.queue = ProductFilterAssignmentQueue()

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def get_runtime_state(self) -> FilterAssignmentRuntimeState:
        return self.assignments.get_or_create_runtime_state()

    def request_full_rebuild(self) -> int:
        state = self.assignments.get_or_create_runtime_state(for_update=True)
        next_revision = max(int(state.target_revision or 0), int(state.applied_revision or 0)) + 1
        state.target_revision = next_revision
        state.rebuild_requested_at = self._utcnow()
        state.last_error = None
        self.db.flush()
        return next_revision

    def enqueue_product_ids_after_commit(self, product_ids: set[int] | list[int] | tuple[int, ...]) -> None:
        self.queue.enqueue_product_ids_after_commit(self.db, product_ids)

    @staticmethod
    def _normalize_text_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for raw_item in value:
            item = str(raw_item or "").strip()
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    @staticmethod
    def _normalized_listing_texts(product: Product) -> tuple[list[str], list[str]]:
        title_texts: list[str] = []
        category_and_tag_texts: list[str] = []
        for membership in product.memberships:
            listing = membership.listing
            if listing is None:
                continue
            title = normalize_haystack(str(listing.source_title or ""))
            if title:
                title_texts.append(title)
            category = normalize_haystack(str(listing.source_category_raw or ""))
            if category:
                category_and_tag_texts.append(category)
            for tag in ProductFilterAssignmentService._normalize_text_list(getattr(listing, "source_tags", None)):
                normalized_tag = normalize_haystack(str(tag))
                if normalized_tag:
                    category_and_tag_texts.append(normalized_tag)
        return title_texts, category_and_tag_texts

    @staticmethod
    def _keyword_matches_any(*, keyword: str, texts: list[str]) -> bool:
        normalized_keyword = normalize_keyword(str(keyword or "").strip())
        if not normalized_keyword:
            return False
        return any(
            keyword_matches(haystack=str(text or ""), keyword=normalized_keyword)
            for text in texts
            if str(text or "").strip()
        )

    @staticmethod
    def _keyword_tail_distance(*, keyword: str, texts: list[str]) -> int | None:
        normalized_keyword = normalize_keyword(str(keyword or "").strip())
        if not normalized_keyword:
            return None
        pattern = keyword_pattern(normalized_keyword)
        best_distance: int | None = None
        for text in texts:
            normalized_text = str(text or "").strip()
            if not normalized_text:
                continue
            for match in pattern.finditer(normalized_text):
                distance = len(normalized_text) - int(match.end())
                if best_distance is None or distance < best_distance:
                    best_distance = distance
        return best_distance

    @classmethod
    def _best_title_tail_distance(cls, *, matched_keywords: list[str], texts: list[str]) -> int | None:
        best_distance: int | None = None
        for keyword in matched_keywords:
            distance = cls._keyword_tail_distance(keyword=keyword, texts=texts)
            if distance is None:
                continue
            if best_distance is None or distance < best_distance:
                best_distance = distance
        return best_distance

    @staticmethod
    def _normalized_product_gender(product: Product) -> str:
        normalized = str(getattr(product, "gender", "") or "").strip().lower()
        if normalized in {"male", "female", "unisex"}:
            return normalized
        return "unisex"

    def _filter_allowed_genders_by_slug(self) -> dict[str, set[str]]:
        filters_by_id = {
            int(entity.id): entity
            for entity in self.taxonomy.list_filters()
        }
        nodes = self.taxonomy.list_filter_nodes()
        node_by_filter_id = {
            int(node.filter_id): node
            for node in nodes
        }
        children_by_parent: dict[int | None, list] = {}
        for node in nodes:
            parent_id = int(node.parent_node_id) if node.parent_node_id is not None else None
            children_by_parent.setdefault(parent_id, []).append(node)
        for bucket in children_by_parent.values():
            bucket.sort(key=lambda item: (int(item.position), int(item.id)))

        def collect_visible_leaf_slugs(*, root_filter_id: int, hidden_node_ids: set[int]) -> list[str]:
            root_node = node_by_filter_id.get(int(root_filter_id))
            if root_node is None:
                return []
            result: list[str] = []

            def walk(node) -> None:
                node_id = int(node.id)
                if node_id in hidden_node_ids:
                    return
                visible_children = [
                    child
                    for child in children_by_parent.get(node_id, [])
                    if int(child.id) not in hidden_node_ids
                ]
                if not visible_children:
                    entity = filters_by_id.get(int(node.filter_id))
                    if entity is None or str(entity.node_kind or "").strip() != "filter":
                        return
                    slug = str(entity.slug or "").strip()
                    if slug:
                        result.append(slug)
                    return
                for child in visible_children:
                    walk(child)

            walk(root_node)
            return result

        allowed_genders_by_slug: dict[str, set[str]] = {}
        for category in self.taxonomy.list_showcase_categories():
            scope = str(getattr(category, "code", "") or "").strip().lower()
            if scope == "men":
                allowed_genders = {"male", "unisex"}
            elif scope == "women":
                allowed_genders = {"female"}
            else:
                continue
            for attachment in category.attachments:
                if str(getattr(attachment, "attachment_kind", "") or "").strip() != "filter":
                    continue
                filter_id = int(attachment.filter_id or 0)
                if filter_id <= 0:
                    continue
                hidden_node_ids = {
                    int(hidden.filter_node_id)
                    for hidden in attachment.hidden_nodes
                    if int(hidden.filter_node_id or 0) > 0
                }
                for slug in collect_visible_leaf_slugs(root_filter_id=filter_id, hidden_node_ids=hidden_node_ids):
                    allowed_genders_by_slug.setdefault(slug, set()).update(allowed_genders)
        return allowed_genders_by_slug

    def _enabled_filter_specs(self) -> list[_FilterSpec]:
        specs: list[_FilterSpec] = []
        allowed_genders_by_slug = self._filter_allowed_genders_by_slug()
        for entity in self.taxonomy.list_filters():
            slug = str(entity.slug or "").strip()
            if not slug or not bool(entity.is_enabled):
                continue
            label = str(entity.display_title or entity.title or "").strip()
            specs.append(
                _FilterSpec(
                    id=int(entity.id),
                    slug=slug,
                    label=label or slug,
                    local_keywords=[
                        normalized
                        for normalized in (
                            normalize_keyword(str(row.keyword or "").strip())
                            for row in entity.local_category_keywords
                        )
                        if normalized
                    ],
                    title_keywords=[
                        normalized
                        for normalized in (
                            normalize_keyword(str(row.keyword or "").strip())
                            for row in entity.title_keywords
                        )
                        if normalized
                    ],
                    manual_product_ids={int(link.product_id) for link in entity.manual_products},
                    restrict_by_gender=bool(getattr(entity, "restrict_by_gender", True)),
                    allowed_genders=(
                        set(allowed_genders_by_slug.get(slug, set()))
                        if slug in allowed_genders_by_slug
                        else None
                    ),
                )
            )
        return specs

    def _resolve_assignment(self, *, product: Product, filter_specs: list[_FilterSpec]) -> _AssignmentResult | None:
        title_texts, category_and_tag_texts = self._normalized_listing_texts(product)
        product_gender = self._normalized_product_gender(product)
        best: _AssignmentResult | None = None
        best_filter_id = 0
        for spec in filter_specs:
            if spec.restrict_by_gender and spec.allowed_genders is not None and product_gender not in spec.allowed_genders:
                continue
            manual_rank = 1 if int(product.id) in spec.manual_product_ids else 0
            matched_local_keywords = [
                keyword
                for keyword in spec.local_keywords
                if self._keyword_matches_any(keyword=keyword, texts=category_and_tag_texts)
            ]
            matched_title_keywords = [
                keyword
                for keyword in spec.title_keywords
                if self._keyword_matches_any(keyword=keyword, texts=title_texts)
            ]
            match_score = (
                len(matched_local_keywords) * self._LOCAL_KEYWORD_SCORE
                + len(matched_title_keywords) * self._TITLE_KEYWORD_SCORE
            )
            if manual_rank == 0 and match_score == 0:
                continue
            title_tail_distance = self._best_title_tail_distance(
                matched_keywords=matched_title_keywords,
                texts=title_texts,
            )
            candidate = _AssignmentResult(
                product_id=int(product.id),
                filter_slug=spec.slug,
                filter_label=spec.label,
                manual_rank=manual_rank,
                match_score=match_score,
                title_tail_distance=title_tail_distance,
                matched_local_keywords=matched_local_keywords,
                matched_title_keywords=matched_title_keywords,
            )
            if best is None:
                best = candidate
                best_filter_id = int(spec.id)
                continue
            if manual_rank > best.manual_rank:
                best = candidate
                best_filter_id = int(spec.id)
                continue
            if manual_rank == best.manual_rank and match_score > best.match_score:
                best = candidate
                best_filter_id = int(spec.id)
                continue
            if (
                manual_rank == best.manual_rank
                and match_score == best.match_score
                and candidate.title_tail_distance is not None
                and best.title_tail_distance is not None
                and candidate.title_tail_distance < best.title_tail_distance
            ):
                best = candidate
                best_filter_id = int(spec.id)
                continue
            if manual_rank == best.manual_rank and match_score == best.match_score and spec.id < best_filter_id:
                best = candidate
                best_filter_id = int(spec.id)
        return best

    def _replace_revision_assignments_for_product_ids(self, *, revision: int, product_ids: list[int]) -> int:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        self.assignments.delete_revision_for_product_ids(revision=int(revision), product_ids=normalized_ids)
        if not normalized_ids:
            self.db.flush()
            return 0

        filter_specs = self._enabled_filter_specs()
        if not filter_specs:
            self.db.flush()
            return 0

        products = self.products.list_products_for_filter_assignment_by_ids(normalized_ids)
        rows: list[dict] = []
        for product in products:
            assignment = self._resolve_assignment(product=product, filter_specs=filter_specs)
            if assignment is None:
                continue
            rows.append(
                {
                    "product_id": int(assignment.product_id),
                    "revision": int(revision),
                    "filter_slug": assignment.filter_slug,
                    "filter_label": assignment.filter_label,
                    "manual_rank": int(assignment.manual_rank),
                    "match_score": int(assignment.match_score),
                    "matched_local_keywords": list(assignment.matched_local_keywords),
                    "matched_title_keywords": list(assignment.matched_title_keywords),
                }
            )
        if rows:
            self.db.bulk_insert_mappings(ProductFilterAssignment, rows)
        self.db.flush()
        return len(rows)

    def refresh_current_revision_product_ids(self, product_ids: set[int] | list[int] | tuple[int, ...]) -> int:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        state = self.assignments.get_or_create_runtime_state(for_update=True)
        current_revision = int(state.applied_revision or 0)
        if current_revision <= 0:
            self.db.flush()
            return 0
        applied = self._replace_revision_assignments_for_product_ids(
            revision=current_revision,
            product_ids=normalized_ids,
        )
        self.db.commit()
        if normalized_ids:
            from app.services.settings.weight_rule_service import WeightRuleService

            WeightRuleService(self.db).enqueue_product_ids(normalized_ids)
        return applied

    def rebuild_pending_revision(self, *, batch_size: int) -> int:
        state = self.assignments.get_or_create_runtime_state(for_update=True)
        target_revision = int(state.target_revision or 0)
        applied_revision = int(state.applied_revision or 0)
        if target_revision <= applied_revision:
            self.db.rollback()
            return 0
        if state.rebuild_started_at is not None:
            self.db.rollback()
            return 0

        state.rebuild_started_at = self._utcnow()
        state.last_error = None
        self.db.commit()

        try:
            self.assignments.delete_revision(target_revision)
            self.db.commit()

            total_products = self.products.count_active_products()
            offset = 0
            while offset < total_products:
                product_ids = self.products.list_active_product_ids_page(limit=batch_size, offset=offset)
                if not product_ids:
                    break
                self._replace_revision_assignments_for_product_ids(revision=target_revision, product_ids=product_ids)
                self.db.commit()
                offset += len(product_ids)

            state = self.assignments.get_or_create_runtime_state(for_update=True)
            if int(state.target_revision or 0) != target_revision:
                self.assignments.delete_revision(target_revision)
                state.rebuild_started_at = None
                self.db.commit()
                return 0
            state.applied_revision = target_revision
            state.rebuild_completed_at = self._utcnow()
            state.rebuild_started_at = None
            state.last_error = None
            self.assignments.delete_other_revisions(keep_revision=target_revision)
            self.db.commit()
            from app.services.settings.weight_rule_service import WeightRuleService

            WeightRuleService(self.db).enqueue_all_active_products()
            return target_revision
        except Exception as exc:
            LOGGER.exception("Filter assignment rebuild failed: %s", exc)
            self.db.rollback()
            state = self.assignments.get_or_create_runtime_state(for_update=True)
            state.rebuild_started_at = None
            state.last_error = str(exc)[:2048]
            self.db.commit()
            raise

    def sync_all_now(self) -> int:
        revision = self.request_full_rebuild()
        self.db.commit()
        return self.rebuild_pending_revision(batch_size=5000) or revision
