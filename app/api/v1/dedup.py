"""API for duplicate candidates and moderation actions."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.parser import (
    DedupCandidateListResponse,
    DedupCombineRequest,
    DedupDecisionListResponse,
    DedupMergeRequest,
    DedupRejectRequest,
    DedupUndoRequest,
)
from app.services.auth.admin_auth_service import require_permission
from app.services.moderation.dedup_service import DedupService

router = APIRouter(tags=["dedup"])


@router.get(
    "/dedup/candidates",
    response_model=DedupCandidateListResponse,
    dependencies=[Depends(require_permission("control.dedup.read"))],
)
def get_dedup_candidates(
    limit: int = Query(settings.dedup_candidates_default_limit, ge=1, le=settings.dedup_candidates_max_limit),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return DedupService(db).get_candidates(limit=limit, offset=offset)


@router.post("/dedup/merge", dependencies=[Depends(require_permission("control.dedup.edit"))])
def merge_duplicate(payload: DedupMergeRequest, db: Session = Depends(get_db)):
    return DedupService(db).merge_duplicate(payload)


@router.post("/dedup/reject", dependencies=[Depends(require_permission("control.dedup.edit"))])
def reject_duplicate(payload: DedupRejectRequest, db: Session = Depends(get_db)):
    return DedupService(db).reject_duplicate(payload)


@router.post("/dedup/combine", dependencies=[Depends(require_permission("control.dedup.edit"))])
def combine_duplicate(payload: DedupCombineRequest, db: Session = Depends(get_db)):
    return DedupService(db).combine_duplicate(payload)


@router.get(
    "/dedup/decisions",
    response_model=DedupDecisionListResponse,
    dependencies=[Depends(require_permission("control.dedup.read"))],
)
def get_dedup_decisions(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return DedupService(db).get_decisions(limit=limit, offset=offset)


@router.post("/dedup/undo", dependencies=[Depends(require_permission("control.dedup.edit"))])
def undo_dedup_decision(payload: DedupUndoRequest, db: Session = Depends(get_db)):
    return DedupService(db).undo_decision(payload)
