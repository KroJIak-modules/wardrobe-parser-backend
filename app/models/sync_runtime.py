from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base


class SyncAppliedBatch(Base):
    __tablename__ = "sync_applied_batch"

    id = Column(Integer, primary_key=True)
    aggregate_job_id = Column(String(64), nullable=False)
    service_job_id = Column(String(64), nullable=False)
    batch_id = Column(String(255), nullable=False)
    source_key = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("service_job_id", "batch_id", name="uq_sync_applied_batch_service_job_batch"),
    )
