from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Filter, FilterAssignmentRuntimeState, ProductFilterAssignment
from app.repositories.catalog_taxonomy import CatalogTaxonomyRepository
from app.repositories.catalog_settings import CatalogWeightRuleRepository
from app.schemas.admin_settings import (
    FilterWeightRuleResponse,
    FilterWeightRuleUpdateRequest,
)

class FilterWeightRuleService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.taxonomy = CatalogTaxonomyRepository(db)
        self.weight_rules = CatalogWeightRuleRepository(db)

    def _leaf_filters(self) -> list[Filter]:
        return [
            entity
            for entity in self.taxonomy.list_filters()
            if str(entity.node_kind or "").strip() == "filter"
        ]

    def _product_counts_by_slug(self) -> dict[str, int]:
        state = self.db.query(FilterAssignmentRuntimeState).filter(FilterAssignmentRuntimeState.id == 1).one_or_none()
        revision = int(getattr(state, "applied_revision", 0) or 0)
        if revision <= 0:
            return {}
        return {
            str(slug): int(count)
            for slug, count in (
                self.db.query(
                    ProductFilterAssignment.filter_slug,
                    func.count(func.distinct(ProductFilterAssignment.product_id)),
                )
                .filter(ProductFilterAssignment.revision == revision)
                .group_by(ProductFilterAssignment.filter_slug)
                .all()
            )
            if str(slug or "").strip()
        }

    def list_mappings(self) -> list[FilterWeightRuleResponse]:
        product_counts_by_slug = self._product_counts_by_slug()
        return [
            FilterWeightRuleResponse(
                filter_slug=str(entity.slug),
                filter_title=str(entity.title),
                display_title=(str(entity.display_title) if entity.display_title else None),
                product_count=int(product_counts_by_slug.get(str(entity.slug), 0)),
                weight_rule_id=(int(entity.default_weight_rule_id) if entity.default_weight_rule_id is not None else None),
                weight_grams=(
                    int(entity.default_weight_rule.weight_grams)
                    if entity.default_weight_rule is not None and entity.default_weight_rule.weight_grams is not None
                    else None
                ),
            )
            for entity in sorted(self._leaf_filters(), key=lambda item: int(item.id))
            if str(entity.slug or "").strip()
        ]

    def update_mappings(self, payload: FilterWeightRuleUpdateRequest) -> list[FilterWeightRuleResponse]:
        leaf_filters = self._leaf_filters()
        filter_by_slug = {
            str(entity.slug): entity
            for entity in leaf_filters
            if str(entity.slug or "").strip()
        }
        active_rules = {
            int(rule.id): rule
            for rule in self.weight_rules.list_active()
            if rule.id is not None
        }

        requested_by_slug: dict[str, int | None] = {}
        for item in payload.items:
            slug = str(item.filter_slug or "").strip()
            if not slug:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="filter_slug is required")
            if slug not in filter_by_slug:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Фильтр не найден: {slug}")
            if item.weight_rule_id is not None and int(item.weight_rule_id) not in active_rules:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Правило веса не найдено: {item.weight_rule_id}")
            requested_by_slug[slug] = int(item.weight_rule_id) if item.weight_rule_id is not None else None

        changed_slugs: list[str] = []
        for slug, weight_rule_id in requested_by_slug.items():
            entity = filter_by_slug[slug]
            current = int(entity.default_weight_rule_id) if entity.default_weight_rule_id is not None else None
            if current == weight_rule_id:
                continue
            entity.default_weight_rule_id = weight_rule_id
            changed_slugs.append(slug)

        if changed_slugs:
            self.db.commit()
        return self.list_mappings()
