from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.database import get_db
from app.core.database import utcnow
from app.models import AdminUiSettings, ProductDedupCandidate
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.dedup_runtime import dedup_scan_runtime
from app.services.catalog.dedup_service_v2 import DedupServiceV2
from app.services.catalog.product_query_service import ProductQueryService


router = APIRouter(tags=["dedup"])


class MergeRequest(BaseModel):
    product_ids: list[int] = Field(min_length=2)
    primary_product_id: int | None = Field(default=None, ge=1)
    primary_listing_id: int | None = Field(default=None, ge=1)
    merge_mode: str | None = Field(default=None)


class RejectRequest(BaseModel):
    product_ids: list[int] = Field(min_length=2)


def _ensure_admin_ui_settings(db: Session) -> AdminUiSettings:
    row = db.query(AdminUiSettings).filter(AdminUiSettings.id == 1).one_or_none()
    if row is None:
        row = AdminUiSettings(id=1)
        db.add(row)
        db.flush()
    return row


@router.get("/dedup/status", dependencies=[Depends(require_permission("control.dedup.read"))])
def get_dedup_status(db: Session = Depends(get_db)) -> dict:
    payload = dedup_scan_runtime.get_state()
    row = db.query(AdminUiSettings).filter(AdminUiSettings.id == 1).one_or_none()
    fallback_last_finished_at = db.query(func.max(ProductDedupCandidate.updated_at)).scalar()
    if row is not None:
        if payload.get("started_at") is None and getattr(row, "dedup_last_started_at", None) is not None:
            payload["started_at"] = row.dedup_last_started_at.isoformat()
        if payload.get("finished_at") is None and getattr(row, "dedup_last_finished_at", None) is not None:
            payload["finished_at"] = row.dedup_last_finished_at.isoformat()
    if payload.get("started_at") is None and fallback_last_finished_at is not None:
        payload["started_at"] = fallback_last_finished_at.isoformat()
    if payload.get("finished_at") is None and fallback_last_finished_at is not None:
        payload["finished_at"] = fallback_last_finished_at.isoformat()
    return payload


@router.post("/dedup/scan", dependencies=[Depends(require_permission("control.dedup.edit"))])
def run_dedup_scan() -> dict:
    def _task() -> int:
        db = SessionLocal()
        try:
            settings_row = _ensure_admin_ui_settings(db)
            settings_row.dedup_last_started_at = utcnow()
            settings_row.dedup_last_finished_at = None
            db.flush()
            service = DedupServiceV2(db)
            candidate_count = service.scan_and_replace_candidates()
            settings_row.dedup_last_finished_at = utcnow()
            db.commit()
            return candidate_count
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    started = dedup_scan_runtime.try_start(task=_task)
    return {
        "ok": bool(started),
        "started": bool(started),
        "status": dedup_scan_runtime.get_state(),
    }


@router.get("/dedup/candidates", dependencies=[Depends(require_permission("control.dedup.read"))])
def list_dedup_candidates(
    limit: int = Query(default=30, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    service = DedupServiceV2(db)
    return service.list_candidates(limit=limit, offset=offset)


@router.post("/dedup/merge", dependencies=[Depends(require_permission("control.dedup.edit"))])
def merge_dedup_pair(payload: MergeRequest, db: Session = Depends(get_db)) -> dict:
    product_id = DedupServiceV2(db).merge(
        product_ids=[int(product_id) for product_id in payload.product_ids],
        primary_product_id=(int(payload.primary_product_id) if payload.primary_product_id is not None else None),
        primary_listing_id=(int(payload.primary_listing_id) if payload.primary_listing_id is not None else None),
        merge_mode=payload.merge_mode,
    )
    db.commit()
    return {"ok": True, "created_product_id": product_id}


@router.post("/dedup/reject", dependencies=[Depends(require_permission("control.dedup.edit"))])
def reject_dedup_pair(payload: RejectRequest, db: Session = Depends(get_db)) -> dict:
    DedupServiceV2(db).reject(product_ids=[int(product_id) for product_id in payload.product_ids])
    db.commit()
    return {"ok": True}


@router.post("/dedup/decisions/{decision_id}/undo", dependencies=[Depends(require_permission("control.dedup.edit"))])
def undo_dedup_decision(decision_id: int, db: Session = Depends(get_db)) -> dict:
    DedupServiceV2(db).undo(decision_id=int(decision_id))
    db.commit()
    return {"ok": True}


@router.get("/dedup/decisions", dependencies=[Depends(require_permission("control.dedup.read"))])
def list_dedup_decisions(
    limit: int = Query(default=30, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    service = DedupServiceV2(db)
    repo = service.decisions
    product_query = ProductQueryService(db)
    items: list[dict] = []
    total = repo.count_decisions()
    decisions = repo.list_decisions(limit=limit + 1, offset=offset)
    has_more = len(decisions) > limit
    decision_rows = decisions[:limit]
    product_ids: set[int] = set()
    for decision in decision_rows:
        product_ids.update(int(member.product_id) for member in getattr(decision, "members", []))
        if decision.created_product_id is not None:
            product_ids.add(int(decision.created_product_id))
    payloads_by_id = product_query.get_dedup_payloads_by_ids(sorted(product_ids))
    for decision in decision_rows:
        member_ids = sorted({int(member.product_id) for member in decision.members} if hasattr(decision, "members") else [])
        members = [payloads_by_id[product_id] for product_id in member_ids if product_id in payloads_by_id]
        if len(members) < 2:
            continue
        created_product = (
            payloads_by_id.get(int(decision.created_product_id))
            if decision.created_product_id is not None
            else None
        )
        can_undo, undo_blocked_reason = service.can_undo_decision(decision)
        items.append(
            {
                "id": int(decision.id),
                "pair_key": ":".join(str(member["id"]) for member in members),
                "action": service.describe_decision_action(decision),
                "decided_at": decision.created_at.isoformat() if decision.created_at else None,
                "members": members,
                "created_product": created_product,
                "can_undo": can_undo,
                "undo_blocked_reason": undo_blocked_reason,
            }
        )
    return {"items": items, "total": total, "has_more": has_more, "limit": limit, "offset": offset}


@router.get("/dedup/decisions/count", dependencies=[Depends(require_permission("control.dedup.read"))])
def count_dedup_decisions(db: Session = Depends(get_db)) -> dict:
    total = DedupServiceV2(db).count_decisions()
    return {"total": total}
