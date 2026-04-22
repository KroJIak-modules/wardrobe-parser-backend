"""Precomputed category index for fast product/category reads."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models import ParserBrandMapping, ParserCategory, ParserCategoryKeyword, ParserCategoryManualProduct, ParserProduct
from app.repositories import (
    ParserCategoryCountSnapshotRepository,
    ParserCategoryIndexStateRepository,
    ParserProductCategoryMatchRepository,
)


class CategoryIndexService:
    MIN_REBUILD_INTERVAL_SEC = 120

    def __init__(self, db: Session):
        self.db = db
        self.match_repo = ParserProductCategoryMatchRepository(db)
        self.count_repo = ParserCategoryCountSnapshotRepository(db)
        self.state_repo = ParserCategoryIndexStateRepository(db)

    def ensure_fresh(self, *, require_counts: bool = False, allow_match_rebuild: bool = True) -> None:
        if self._should_rebuild_matches():
            # Heavy full match rebuild may take long under active sync load.
            # For latency-sensitive read endpoints we can skip blocking rebuild
            # and serve using the last consistent snapshot.
            if allow_match_rebuild:
                self.rebuild_full()
                return
        if require_counts and (self._snapshot_is_empty() or self._should_rebuild_counts()):
            self.rebuild_counts()

    def rebuild_full(self) -> None:
        self._rebuild_auto_matches()
        self._sync_manual_matches()
        self._rebuild_counts_snapshot()
        now = datetime.now(timezone.utc)
        state = self.state_repo.get_or_create_singleton()
        state.matches_built_at = now
        state.counts_built_at = now
        self.db.commit()

    def rebuild_counts(self) -> None:
        self._rebuild_counts_snapshot()
        now = datetime.now(timezone.utc)
        state = self.state_repo.get_or_create_singleton()
        if state.matches_built_at is None:
            state.matches_built_at = now
        state.counts_built_at = now
        self.db.commit()

    def get_snapshot_counts(self) -> dict[int, int]:
        return self.count_repo.get_subtree_count_map()

    def get_grouped_category_ids(self, product_ids: set[int]) -> dict[int, list[int]]:
        return self.match_repo.get_grouped_category_ids(product_ids)

    def sync_manual_link(self, *, category_id: int, product_id: int) -> None:
        self.db.execute(
            text(
                """
                INSERT INTO parser_product_category_match (product_id, category_id, match_source, score, created_at, updated_at)
                VALUES (:product_id, :category_id, 'manual', 1000000, now(), now())
                ON CONFLICT (product_id, category_id, match_source)
                DO UPDATE SET score = EXCLUDED.score, updated_at = now()
                """
            ),
            {"product_id": int(product_id), "category_id": int(category_id)},
        )
        state = self.state_repo.get_or_create_singleton()
        now = datetime.now(timezone.utc)
        state.matches_built_at = now
        state.counts_built_at = None
        self.db.commit()

    def remove_manual_link(self, *, category_id: int, product_id: int) -> None:
        self.db.execute(
            text(
                """
                DELETE FROM parser_product_category_match
                WHERE product_id = :product_id
                  AND category_id = :category_id
                  AND match_source = 'manual'
                """
            ),
            {"product_id": int(product_id), "category_id": int(category_id)},
        )
        state = self.state_repo.get_or_create_singleton()
        now = datetime.now(timezone.utc)
        state.matches_built_at = now
        state.counts_built_at = None
        self.db.commit()

    def sync_manual_links_for_product(self, *, product_id: int) -> None:
        pid = int(product_id)
        self.db.execute(
            text(
                """
                DELETE FROM parser_product_category_match
                WHERE product_id = :product_id
                  AND match_source = 'manual'
                """
            ),
            {"product_id": pid},
        )
        self.db.execute(
            text(
                """
                INSERT INTO parser_product_category_match (product_id, category_id, match_source, score, created_at, updated_at)
                SELECT
                    m.product_id,
                    m.category_id,
                    'manual',
                    1000000,
                    now(),
                    now()
                FROM parser_category_manual_product m
                JOIN parser_product p ON p.id = m.product_id AND p.deleted_at IS NULL
                JOIN parser_category c ON c.id = m.category_id AND c.deleted_at IS NULL
                WHERE m.product_id = :product_id
                ON CONFLICT (product_id, category_id, match_source)
                DO UPDATE SET score = EXCLUDED.score, updated_at = now()
                """
            ),
            {"product_id": pid},
        )
        state = self.state_repo.get_or_create_singleton()
        now = datetime.now(timezone.utc)
        state.matches_built_at = now
        state.counts_built_at = None
        self.db.commit()

    def mark_counts_stale(self) -> None:
        state = self.state_repo.get_or_create_singleton()
        state.counts_built_at = None
        self.db.commit()

    def refresh_auto_matches_for_category(self, *, category_id: int) -> None:
        cid = int(category_id)
        self.db.execute(
            text(
                """
                DELETE FROM parser_product_category_match
                WHERE category_id = :category_id
                  AND match_source = 'auto'
                """
            ),
            {"category_id": cid},
        )
        self.db.execute(
            text(
                """
                INSERT INTO parser_product_category_match (product_id, category_id, match_source, score, created_at, updated_at)
                WITH product_with_vendor AS (
                    SELECT
                        p.id,
                        p.title,
                        p.product_type,
                        p.status,
                        p.deleted_at,
                        trim(coalesce(
                            (
                                SELECT bm.target_brand
                                FROM parser_brand_mapping bm
                                WHERE bm.source_brand_key = regexp_replace(lower(trim(coalesce(p.vendor, ''))), '[^[:alnum:]]+', '', 'g')
                                LIMIT 1
                            ),
                            p.vendor
                        )) AS mapped_vendor
                    FROM parser_product p
                )
                SELECT
                    p.id AS product_id,
                    :category_id AS category_id,
                    'auto' AS match_source,
                    SUM(char_length(k.keyword))::int AS score,
                    now(),
                    now()
                FROM product_with_vendor p
                JOIN parser_category c ON c.id = :category_id
                JOIN parser_category_keyword k ON k.category_id = c.id
                WHERE p.deleted_at IS NULL
                  AND c.deleted_at IS NULL
                  AND c.is_fallback IS FALSE
                  AND c.is_enabled IS TRUE
                  AND char_length(trim(k.keyword)) > 0
                  AND (
                    (k.keyword_scope = 'local' AND strpos(lower(trim(coalesce(p.mapped_vendor, '') || ' ' || coalesce(p.product_type, ''))), lower(k.keyword)) > 0)
                    OR
                    (k.keyword_scope = 'title' AND strpos(lower(trim(coalesce(p.title, ''))), lower(k.keyword)) > 0)
                    OR
                    (k.keyword_scope = 'status' AND lower(trim(p.status::text)) = lower(trim(k.keyword)))
                  )
                GROUP BY p.id
                ON CONFLICT (product_id, category_id, match_source)
                DO UPDATE SET
                    score = EXCLUDED.score,
                    updated_at = now()
                """
            ),
            {"category_id": cid},
        )
        now = datetime.now(timezone.utc)
        state = self.state_repo.get_or_create_singleton()
        state.matches_built_at = now
        state.counts_built_at = None
        self.db.commit()

    def _snapshot_is_empty(self) -> bool:
        total = self.db.query(func.count(ParserCategory.id)).filter(ParserCategory.deleted_at.is_(None)).scalar() or 0
        if total == 0:
            return False
        snapshot_total = self.count_repo.query().count()
        return int(snapshot_total) == 0

    def _should_rebuild_matches(self) -> bool:
        state = self.state_repo.get_or_create_singleton()
        if state.matches_built_at is None:
            return True

        now = datetime.now(timezone.utc)
        if state.updated_at is not None:
            elapsed = (now - state.updated_at).total_seconds()
            if elapsed < self.MIN_REBUILD_INTERVAL_SEC:
                return False

        matches_built_at = state.matches_built_at
        latest_product = self.db.query(func.max(ParserProduct.updated_at)).filter(ParserProduct.deleted_at.is_(None)).scalar()
        latest_keyword = self.db.query(func.max(ParserCategoryKeyword.created_at)).scalar()
        latest_manual = self.db.query(func.max(ParserCategoryManualProduct.created_at)).scalar()
        latest_brand_mapping = self.db.query(func.max(ParserBrandMapping.updated_at)).scalar()

        candidates = [latest_product, latest_keyword, latest_manual, latest_brand_mapping]
        for item in candidates:
            if item is not None and item > matches_built_at:
                return True
        return False

    def _should_rebuild_counts(self) -> bool:
        state = self.state_repo.get_or_create_singleton()
        if state.counts_built_at is None:
            return True
        counts_built_at = state.counts_built_at
        latest_category = self.db.query(func.max(ParserCategory.updated_at)).filter(ParserCategory.deleted_at.is_(None)).scalar()
        return bool(latest_category is not None and latest_category > counts_built_at)

    def _sync_manual_matches(self) -> None:
        self.db.execute(text("DELETE FROM parser_product_category_match WHERE match_source = 'manual'"))
        self.db.execute(
            text(
                """
                INSERT INTO parser_product_category_match (product_id, category_id, match_source, score, created_at, updated_at)
                SELECT
                    m.product_id,
                    m.category_id,
                    'manual',
                    1000000,
                    now(),
                    now()
                FROM parser_category_manual_product m
                JOIN parser_product p ON p.id = m.product_id AND p.deleted_at IS NULL
                JOIN parser_category c ON c.id = m.category_id AND c.deleted_at IS NULL
                """
            )
        )

    def _rebuild_auto_matches(self) -> None:
        self.db.execute(text("DELETE FROM parser_product_category_match WHERE match_source IN ('auto', 'designer')"))
        self.db.execute(
            text(
                """
                INSERT INTO parser_product_category_match (product_id, category_id, match_source, score, created_at, updated_at)
                WITH designers_root AS (
                    SELECT c.id
                    FROM parser_category c
                    WHERE c.deleted_at IS NULL
                      AND c.parent_id IS NULL
                      AND (
                        c.slug = 'dizaynery'
                        OR lower(trim(c.name)) = 'дизайнеры'
                      )
                    ORDER BY c.id
                    LIMIT 1
                ),
                designers_branch AS (
                    SELECT c.id, c.name
                    FROM parser_category c
                    JOIN designers_root r ON c.parent_id = r.id
                    WHERE c.deleted_at IS NULL
                ),
                product_with_vendor AS (
                    SELECT
                        p.id,
                        p.title,
                        p.product_type,
                        p.status,
                        p.deleted_at,
                        trim(coalesce(
                            (
                                SELECT bm.target_brand
                                FROM parser_brand_mapping bm
                                WHERE bm.source_brand_key = regexp_replace(lower(trim(coalesce(p.vendor, ''))), '[^[:alnum:]]+', '', 'g')
                                LIMIT 1
                            ),
                            p.vendor
                        )) AS mapped_vendor
                    FROM parser_product p
                ),
                auto_matches AS (
                    SELECT
                        p.id AS product_id,
                        k.category_id,
                        'auto' AS match_source,
                        SUM(char_length(k.keyword))::int AS score
                    FROM product_with_vendor p
                    JOIN parser_category_keyword k ON TRUE
                    JOIN parser_category c ON c.id = k.category_id
                    WHERE p.deleted_at IS NULL
                      AND c.deleted_at IS NULL
                      AND c.is_fallback IS FALSE
                      AND c.is_enabled IS TRUE
                      AND char_length(trim(k.keyword)) > 0
                      AND (
                        (k.keyword_scope = 'local' AND strpos(lower(trim(coalesce(p.mapped_vendor, '') || ' ' || coalesce(p.product_type, ''))), lower(k.keyword)) > 0)
                        OR
                        (k.keyword_scope = 'title' AND strpos(lower(trim(coalesce(p.title, ''))), lower(k.keyword)) > 0)
                        OR
                        (k.keyword_scope = 'status' AND lower(trim(p.status::text)) = lower(trim(k.keyword)))
                      )
                    GROUP BY p.id, k.category_id
                ),
                designer_matches AS (
                    SELECT
                        p.id AS product_id,
                        d.id AS category_id,
                        'designer' AS match_source,
                        GREATEST(char_length(trim(d.name)), 1)::int AS score
                    FROM product_with_vendor p
                    JOIN designers_branch d ON TRUE
                    JOIN parser_category c ON c.id = d.id
                    WHERE p.deleted_at IS NULL
                      AND c.is_enabled IS TRUE
                      AND char_length(trim(coalesce(p.mapped_vendor, ''))) > 0
                      AND lower(trim(coalesce(p.mapped_vendor, ''))) = lower(trim(d.name))
                )
                SELECT product_id, category_id, match_source, score, now(), now()
                FROM auto_matches
                UNION ALL
                SELECT product_id, category_id, match_source, score, now(), now()
                FROM designer_matches
                ON CONFLICT (product_id, category_id, match_source)
                DO UPDATE SET
                    score = EXCLUDED.score,
                    updated_at = now()
                """
            )
        )

    def _rebuild_counts_snapshot(self) -> None:
        self.db.execute(text("DELETE FROM parser_category_count_snapshot"))
        self.db.execute(
            text(
                """
                INSERT INTO parser_category_count_snapshot (category_id, direct_count, subtree_count, updated_at)
                WITH RECURSIVE category_paths AS (
                    SELECT c.id AS ancestor_id, c.id AS descendant_id
                    FROM parser_category c
                    WHERE c.deleted_at IS NULL

                    UNION ALL

                    SELECT p.ancestor_id, c.id AS descendant_id
                    FROM category_paths p
                    JOIN parser_category c ON c.parent_id = p.descendant_id
                    WHERE c.deleted_at IS NULL
                ),
                direct_products AS (
                    SELECT DISTINCT m.category_id, m.product_id
                    FROM parser_product_category_match m
                    JOIN parser_product p ON p.id = m.product_id
                    JOIN parser_category c ON c.id = m.category_id
                    WHERE p.deleted_at IS NULL
                      AND c.deleted_at IS NULL
                      AND c.is_enabled IS TRUE
                      AND c.is_fallback IS FALSE
                ),
                fallback_category AS (
                    SELECT c.id AS category_id
                    FROM parser_category c
                    WHERE c.deleted_at IS NULL
                      AND c.is_fallback IS TRUE
                    ORDER BY c.id
                    LIMIT 1
                ),
                fallback_products AS (
                    SELECT fc.category_id, p.id AS product_id
                    FROM parser_product p
                    JOIN fallback_category fc ON TRUE
                    WHERE p.deleted_at IS NULL
                      AND NOT EXISTS (
                        SELECT 1
                        FROM parser_product_category_match m
                        JOIN parser_category c ON c.id = m.category_id
                        WHERE m.product_id = p.id
                          AND c.deleted_at IS NULL
                          AND c.is_enabled IS TRUE
                          AND c.is_fallback IS FALSE
                      )
                ),
                all_direct_products AS (
                    SELECT category_id, product_id
                    FROM direct_products
                    UNION ALL
                    SELECT category_id, product_id
                    FROM fallback_products
                ),
                direct_counts AS (
                    SELECT category_id, count(DISTINCT product_id)::int AS direct_count
                    FROM all_direct_products
                    GROUP BY category_id
                ),
                subtree_counts AS (
                    SELECT cp.ancestor_id AS category_id, count(DISTINCT adp.product_id)::int AS subtree_count
                    FROM category_paths cp
                    LEFT JOIN all_direct_products adp ON adp.category_id = cp.descendant_id
                    GROUP BY cp.ancestor_id
                )
                SELECT
                    c.id AS category_id,
                    coalesce(dc.direct_count, 0)::int AS direct_count,
                    coalesce(sc.subtree_count, 0)::int AS subtree_count,
                    now() AS updated_at
                FROM parser_category c
                LEFT JOIN direct_counts dc ON dc.category_id = c.id
                LEFT JOIN subtree_counts sc ON sc.category_id = c.id
                WHERE c.deleted_at IS NULL
                """
            )
        )
