from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, func

from app.core.database import Base


class CatalogPresenceState(Base):
    """Last observed product presence and the latest automatic reappearance."""

    __tablename__ = "catalog_presence_state"

    entity_kind = Column(String(16), primary_key=True)
    entity_key = Column(String(255), primary_key=True)
    product_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    is_auto_tracked = Column(Boolean, nullable=False, default=True, server_default="true")
    last_reappeared_at = Column(DateTime(timezone=True), nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("entity_kind IN ('product', 'brand', 'designer')", name="ck_catalog_presence_state_entity_kind"),
        CheckConstraint("product_count >= 0", name="ck_catalog_presence_state_product_count_non_negative"),
        Index("ix_catalog_presence_state_reappearance", "entity_kind", "last_reappeared_at"),
    )


class AdminAgainView(Base):
    """Per-admin acknowledgement of the latest AGAIN event for an entity."""

    __tablename__ = "admin_again_view"

    admin_user_id = Column(Integer, ForeignKey("admin_user.id", ondelete="CASCADE"), primary_key=True)
    entity_kind = Column(String(16), primary_key=True)
    entity_key = Column(String(255), primary_key=True)
    viewed_event_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("entity_kind IN ('product', 'brand', 'designer')", name="ck_admin_again_view_entity_kind"),
        Index("ix_admin_again_view_entity", "entity_kind", "entity_key"),
    )
