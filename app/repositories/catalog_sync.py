from __future__ import annotations

from sqlalchemy.orm import Session, joinedload

from app.models import SyncAppliedBatch, SyncJob, SyncJobSourceRun


class CatalogSyncRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_job(self, **kwargs) -> SyncJob:
        entity = SyncJob(**kwargs)
        self.session.add(entity)
        self.session.flush()
        return entity

    def get_job(self, job_id: int) -> SyncJob | None:
        return self.session.query(SyncJob).filter(SyncJob.id == int(job_id)).one_or_none()

    def get_latest_job(self) -> SyncJob | None:
        return self.session.query(SyncJob).order_by(SyncJob.id.desc()).first()

    def create_source_run(self, **kwargs) -> SyncJobSourceRun:
        entity = SyncJobSourceRun(**kwargs)
        self.session.add(entity)
        self.session.flush()
        return entity

    def get_source_run(self, *, sync_job_id: int, source_id: int) -> SyncJobSourceRun | None:
        return (
            self.session.query(SyncJobSourceRun)
            .filter(
                SyncJobSourceRun.sync_job_id == int(sync_job_id),
                SyncJobSourceRun.source_id == int(source_id),
            )
            .one_or_none()
        )

    def get_source_run_by_id(self, source_run_id: int) -> SyncJobSourceRun | None:
        return self.session.query(SyncJobSourceRun).filter(SyncJobSourceRun.id == int(source_run_id)).one_or_none()

    def has_active_job(self) -> bool:
        return (
            self.session.query(SyncJob.id)
            .filter(SyncJob.status.in_(("queued", "running")))
            .first()
            is not None
        )

    def list_source_runs(self, *, sync_job_id: int) -> list[SyncJobSourceRun]:
        return (
            self.session.query(SyncJobSourceRun)
            .options(joinedload(SyncJobSourceRun.source))
            .filter(SyncJobSourceRun.sync_job_id == int(sync_job_id))
            .order_by(SyncJobSourceRun.id.asc())
            .all()
        )

    def has_applied_batch(self, *, source_run_id: int, batch_key: str) -> bool:
        return (
            self.session.query(SyncAppliedBatch)
            .filter(
                SyncAppliedBatch.source_run_id == int(source_run_id),
                SyncAppliedBatch.batch_key == str(batch_key),
            )
            .count()
            > 0
        )

    def mark_applied_batch(self, *, source_run_id: int, batch_key: str) -> SyncAppliedBatch:
        entity = SyncAppliedBatch(source_run_id=int(source_run_id), batch_key=str(batch_key))
        self.session.add(entity)
        self.session.flush()
        return entity
