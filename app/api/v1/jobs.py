from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import ValidationError
from app.services.auth.admin_auth_service import AdminAuthContext, require_admin_access, require_permission
from app.services.catalog.sync_job_service import SyncJobService


router = APIRouter(tags=["jobs"])


class StartSyncRequest(BaseModel):
    sources: list[str] = Field(default_factory=list)


def mark_interrupted_jobs_on_startup() -> None:
    SyncJobService.mark_interrupted_jobs_on_startup()


@router.post("/jobs", dependencies=[Depends(require_permission("control.sources.edit"))])
def start_sync_job(
    payload: StartSyncRequest,
    db: Session = Depends(get_db),
    context: AdminAuthContext = Depends(require_admin_access),
) -> dict:
    try:
        return SyncJobService(db).start_job(
            triggered_by_admin_user_id=context.user_id,
            source_keys=list(payload.sources or []),
            trigger_kind="manual",
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


@router.get("/jobs/latest", dependencies=[Depends(require_permission("control.sources.read"))])
def latest_sync_job(db: Session = Depends(get_db)) -> dict | None:
    return SyncJobService(db).latest()


@router.get("/jobs/{job_id}", dependencies=[Depends(require_permission("control.sources.read"))])
def get_sync_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    return SyncJobService(db).serialize_job(job_id)


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_permission("control.sources.edit"))])
def cancel_sync_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    return SyncJobService(db).cancel(job_id)


@router.get("/jobs", dependencies=[Depends(require_permission("control.sources.read"))])
def list_sync_jobs(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)) -> dict:
    latest = SyncJobService(db).latest()
    if latest is None:
        return {"items": [], "total": 0, "limit": limit, "offset": 0}
    return {"items": [latest], "total": 1, "limit": limit, "offset": 0}
