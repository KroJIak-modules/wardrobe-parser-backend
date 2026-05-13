from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class SyncJobRuntime(Base):
    __tablename__ = "sync_job_runtime"

    id = Column(Integer, primary_key=True)
    aggregate_job_id = Column(String(64), nullable=False, unique=True)
    service_job_id = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    total_sources = Column(Integer, nullable=False, default=0)
    processed_sources = Column(Integer, nullable=False, default=0)
    expected_db_upserts = Column(Integer, nullable=False, default=0)
    db_upserts_done = Column(Integer, nullable=False, default=0)
    failed_products = Column(Integer, nullable=False, default=0)
    current_source_name = Column(String(255), nullable=True)
    current_source_index = Column(Integer, nullable=False, default=0)
    current_stage = Column(String(255), nullable=True)
    products_success = Column(Integer, nullable=False, default=0)
    products_error = Column(Integer, nullable=False, default=0)
    event_cursor = Column(Integer, nullable=False, default=0)
    can_cancel = Column(Boolean, nullable=False, default=True)
    error_message = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
