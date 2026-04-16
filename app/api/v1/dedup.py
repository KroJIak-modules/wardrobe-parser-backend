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
from app.services.moderation.dedup_service import DedupService

router = APIRouter(tags=["dedup"])


@router.get("/dedup/candidates", response_model=DedupCandidateListResponse)
def get_dedup_candidates(
    limit: int = Query(settings.dedup_candidates_default_limit, ge=1, le=settings.dedup_candidates_max_limit),
    db: Session = Depends(get_db),
):
    return DedupService(db).get_candidates(limit=limit)


@router.post("/dedup/merge")
def merge_duplicate(payload: DedupMergeRequest, db: Session = Depends(get_db)):
    return DedupService(db).merge_duplicate(payload)


@router.post("/dedup/reject")
def reject_duplicate(payload: DedupRejectRequest, db: Session = Depends(get_db)):
    return DedupService(db).reject_duplicate(payload)


@router.post("/dedup/combine")
def combine_duplicate(payload: DedupCombineRequest, db: Session = Depends(get_db)):
    return DedupService(db).combine_duplicate(payload)


@router.get("/dedup/decisions", response_model=DedupDecisionListResponse)
def get_dedup_decisions(
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    return DedupService(db).get_decisions(limit=limit)


@router.post("/dedup/undo")
def undo_dedup_decision(payload: DedupUndoRequest, db: Session = Depends(get_db)):
    return DedupService(db).undo_decision(payload)
