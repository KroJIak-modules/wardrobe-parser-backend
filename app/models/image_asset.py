from sqlalchemy import Column, DateTime, Integer, String

from app.core.database import Base


class ImageAsset(Base):
    __tablename__ = "image_asset"

    id = Column(Integer, primary_key=True)
    source_url = Column(String(2048), nullable=False)
    storage_mode = Column(String(50), nullable=False)
    stored_path = Column(String(2048), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
