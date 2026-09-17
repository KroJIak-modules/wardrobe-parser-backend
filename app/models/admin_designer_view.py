"""Per-admin NEW tracking for designer editor entities."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, BigInteger, func

from app.core.database import Base


class AdminBrandView(Base):
    """Source brands this admin has already reviewed. Keyed by source_name because
    designer_source_names rows are deleted and recreated by reconcile."""

    __tablename__ = "admin_brand_view"

    admin_user_id = Column(Integer, ForeignKey("admin_user.id", ondelete="CASCADE"), primary_key=True)
    source_name = Column(String(255), primary_key=True)
    viewed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AdminDesignerView(Base):
    """Catalog designers this admin has already reviewed."""

    __tablename__ = "admin_designer_view"

    admin_user_id = Column(Integer, ForeignKey("admin_user.id", ondelete="CASCADE"), primary_key=True)
    designer_id = Column(BigInteger, ForeignKey("designers.id", ondelete="CASCADE"), primary_key=True)
    viewed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_admin_designer_view_designer_user", "designer_id", "admin_user_id"),
    )
