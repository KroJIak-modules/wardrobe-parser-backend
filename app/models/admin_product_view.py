"""Per-admin product view tracking for the control panel."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, BigInteger, func

from app.core.database import Base


class AdminProductView(Base):
    __tablename__ = "admin_product_view"

    admin_user_id = Column(Integer, ForeignKey("admin_user.id", ondelete="CASCADE"), primary_key=True)
    product_id = Column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    viewed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_admin_product_view_product_user", "product_id", "admin_user_id"),
    )
