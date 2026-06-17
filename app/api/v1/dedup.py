from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.dedup_service_v2 import DedupServiceV2
from app.services.catalog.product_query_service import ProductQueryService


router = APIRouter(tags=["dedup"])


class MergeRequest(BaseModel):
    primary_product_id: int = Field(ge=1)
    duplicate_product_id: int = Field(ge=1)


class RejectRequest(BaseModel):
    product_a_id: int = Field(ge=1)
    product_b_id: int = Field(ge=1)


@router.get("/dedup/candidates", dependencies=[Depends(require_permission("control.dedup.read"))])
def list_dedup_candidates(
    limit: int = Query(default=30, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    service = DedupServiceV2(db)
    builder = ProductQueryService(db).build_product_payload
    return service.list_candidates(limit=limit, offset=offset, payload_builder=builder)


@router.post("/dedup/merge", dependencies=[Depends(require_permission("control.dedup.edit"))])
def merge_dedup_pair(payload: MergeRequest, db: Session = Depends(get_db)) -> dict:
    product_id = DedupServiceV2(db).merge(
        primary_product_id=int(payload.primary_product_id),
        duplicate_product_id=int(payload.duplicate_product_id),
    )
    db.commit()
    return {"ok": True, "created_product_id": product_id}


@router.post("/dedup/reject", dependencies=[Depends(require_permission("control.dedup.edit"))])
def reject_dedup_pair(payload: RejectRequest, db: Session = Depends(get_db)) -> dict:
    DedupServiceV2(db).reject(product_ids=[int(payload.product_a_id), int(payload.product_b_id)])
    db.commit()
    return {"ok": True}


@router.get("/dedup/decisions", dependencies=[Depends(require_permission("control.dedup.read"))])
def list_dedup_decisions(
    limit: int = Query(default=30, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    repo = DedupServiceV2(db).decisions
    product_query = ProductQueryService(db)
    items: list[dict] = []
    for decision in repo.list_decisions(limit=limit, offset=offset):
        member_ids = sorted({int(member.product_id) for member in decision.members} if hasattr(decision, "members") else [])
        left = product_query.get_product_payload(member_ids[0]) if len(member_ids) >= 1 else None
        right = product_query.get_product_payload(member_ids[1]) if len(member_ids) >= 2 else None
        if left is None or right is None:
            continue
        items.append(
            {
                "pair_key": f"{min(int(left['id']), int(right['id']))}:{max(int(left['id']), int(right['id']))}",
                "action": str(decision.decision_kind),
                "decided_at": decision.created_at.isoformat() if decision.created_at else None,
                "can_undo": False,
                "undo_block_reason": "undo_removed",
                "left": left,
                "right": right,
            }
        )
    return {"items": items, "total": len(items), "limit": limit, "offset": offset}
