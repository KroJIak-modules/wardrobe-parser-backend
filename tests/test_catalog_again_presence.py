from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.repositories.catalog_again import AdminAgainViewRepository, CatalogPresenceRepository


def test_again_reappears_for_each_entity_and_can_be_acknowledged() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:12]
    admin_user_id = 1
    try:
        states = CatalogPresenceRepository(db)
        views = AdminAgainViewRepository(db)
        for entity_kind in ("product", "brand", "designer"):
            key = f"again-{entity_kind}-{marker}"
            assert states.sync_counts(entity_kind=entity_kind, counts={key: 0}) == set()
            assert states.sync_counts(entity_kind=entity_kind, counts={key: 1}) == {key}
            assert views.filter_again_keys(
                admin_user_id=admin_user_id,
                entity_kind=entity_kind,
                entity_keys=[key],
            ) == {key}
            assert views.mark_viewed(
                admin_user_id=admin_user_id,
                entity_kind=entity_kind,
                entity_key=key,
            )
            assert views.filter_again_keys(
                admin_user_id=admin_user_id,
                entity_kind=entity_kind,
                entity_keys=[key],
            ) == set()
            states.sync_counts(entity_kind=entity_kind, counts={key: 0})
            states.sync_counts(entity_kind=entity_kind, counts={key: 2})
            assert views.filter_again_keys(
                admin_user_id=admin_user_id,
                entity_kind=entity_kind,
                entity_keys=[key],
            ) == {key}
        db.rollback()
    finally:
        db.close()


def test_manual_brand_state_does_not_emit_again() -> None:
    db = SessionLocal()
    key = f"manual-again-{uuid4().hex[:12]}"
    try:
        states = CatalogPresenceRepository(db)
        views = AdminAgainViewRepository(db)
        states.sync_counts(entity_kind="brand", counts={key: 1}, ignored_keys=[key])
        states.sync_counts(entity_kind="brand", counts={key: 0}, ignored_keys=[key])
        states.sync_counts(entity_kind="brand", counts={key: 1}, ignored_keys=[key])
        assert views.filter_again_keys(
            admin_user_id=1,
            entity_kind="brand",
            entity_keys=[key],
        ) == set()
        db.rollback()
    finally:
        db.close()


def test_partial_product_batch_does_not_reset_other_product_presence() -> None:
    db = SessionLocal()
    try:
        states = CatalogPresenceRepository(db)
        states.sync_counts(entity_kind="product", counts={"1001": 1, "1002": 1})
        states.sync_counts(entity_kind="product", counts={"1001": 0}, complete_snapshot=False)
        states.sync_counts(entity_kind="product", counts={"1001": 1}, complete_snapshot=False)
        views = AdminAgainViewRepository(db)
        assert views.filter_again_keys(
            admin_user_id=1,
            entity_kind="product",
            entity_keys=["1001"],
        ) == {"1001"}
        assert views.filter_again_keys(
            admin_user_id=1,
            entity_kind="product",
            entity_keys=["1002"],
        ) == set()
        db.rollback()
    finally:
        db.close()
