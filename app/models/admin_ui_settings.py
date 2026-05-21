from sqlalchemy import JSON, Boolean, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class AdminUiSettings(Base):
    __tablename__ = "admin_ui_settings"

    id = Column(Integer, primary_key=True)
    designers_min_products = Column(Integer, nullable=False, default=1)
    designers_exclude_store_vendors = Column(Boolean, nullable=False, default=False)
    showcase_hero_image_asset_id = Column(Integer, nullable=True)
    showcase_carousel_image_asset_ids = Column(JSON, nullable=False, default=list)
    auto_sync_period_minutes = Column(Integer, nullable=False, default=60)
    auto_sync_next_run_at = Column(DateTime(timezone=True), nullable=True)
    auto_sync_last_started_at = Column(DateTime(timezone=True), nullable=True)
    auto_sync_last_finished_at = Column(DateTime(timezone=True), nullable=True)
    auto_sync_last_status = Column(String(32), nullable=True)
    auto_sync_last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_admin_ui_settings_updated_at", "updated_at"),
    )
