from sqlalchemy import Boolean, Column, DateTime, Index, Integer, JSON
from sqlalchemy.sql import func

from app.core.database import Base


class AdminUiSettings(Base):
    __tablename__ = "admin_ui_settings"

    id = Column(Integer, primary_key=True)
    designers_min_products = Column(Integer, nullable=False, default=1)
    designers_exclude_store_vendors = Column(Boolean, nullable=False, default=False)
    showcase_hero_image_asset_id = Column(Integer, nullable=True)
    showcase_carousel_image_asset_ids = Column(JSON, nullable=False, default=list)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_admin_ui_settings_updated_at", "updated_at"),
    )
