"""Decision persistence helpers for dedup moderation actions."""

from __future__ import annotations

from datetime import datetime, timezone

from app.repositories import ParserDedupDecisionRepository


def upsert_merge_decision(
    decision_repo: ParserDedupDecisionRepository,
    *,
    pair_key_value: str,
    left_product_id: int,
    right_product_id: int,
    merged_into_product_id: int,
    snapshot_payload: dict | None = None,
    restore_payload: dict | None = None,
) -> None:
    decision = decision_repo.get_by_pair_key(pair_key_value)
    if decision:
        decision.action = "merge"
        decision.left_product_id = left_product_id
        decision.right_product_id = right_product_id
        decision.merged_into_product_id = merged_into_product_id
        decision.snapshot_payload = snapshot_payload
        decision.restore_payload = restore_payload
        decision.decided_at = datetime.now(timezone.utc)
        return

    decision_repo.create(
        pair_key=pair_key_value,
        left_product_id=left_product_id,
        right_product_id=right_product_id,
        action="merge",
        merged_into_product_id=merged_into_product_id,
        snapshot_payload=snapshot_payload,
        restore_payload=restore_payload,
    )


def upsert_combine_decision(
    decision_repo: ParserDedupDecisionRepository,
    *,
    pair_key_value: str,
    left_product_id: int,
    right_product_id: int,
    merged_into_product_id: int,
    snapshot_payload: dict | None = None,
    restore_payload: dict | None = None,
) -> None:
    decision = decision_repo.get_by_pair_key(pair_key_value)
    if decision:
        decision.action = "combine"
        decision.left_product_id = left_product_id
        decision.right_product_id = right_product_id
        decision.merged_into_product_id = merged_into_product_id
        decision.snapshot_payload = snapshot_payload
        decision.restore_payload = restore_payload
        decision.decided_at = datetime.now(timezone.utc)
        return

    decision_repo.create(
        pair_key=pair_key_value,
        left_product_id=left_product_id,
        right_product_id=right_product_id,
        action="combine",
        merged_into_product_id=merged_into_product_id,
        snapshot_payload=snapshot_payload,
        restore_payload=restore_payload,
    )


def upsert_reject_decision(
    decision_repo: ParserDedupDecisionRepository,
    *,
    pair_key_value: str,
    left_product_id: int,
    right_product_id: int,
) -> None:
    decision = decision_repo.get_by_pair_key(pair_key_value)
    if decision:
        decision.action = "reject"
        decision.left_product_id = left_product_id
        decision.right_product_id = right_product_id
        decision.merged_into_product_id = None
        decision.snapshot_payload = None
        decision.restore_payload = None
        decision.decided_at = datetime.now(timezone.utc)
        return

    decision_repo.create(
        pair_key=pair_key_value,
        left_product_id=left_product_id,
        right_product_id=right_product_id,
        action="reject",
        merged_into_product_id=None,
        snapshot_payload=None,
        restore_payload=None,
    )
