"""API for duplicate candidates and moderation actions."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.parser import DedupCandidateListResponse, DedupMergeRequest, DedupRejectRequest
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
