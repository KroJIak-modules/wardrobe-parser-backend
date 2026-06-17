from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKey, String, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class ProductDedupDecision(Base):
    __tablename__ = "product_dedup_decisions"

    id = Column(BigInteger, primary_key=True)
    decision_kind = Column(String(16), nullable=False)
    created_product_id = Column(BigInteger, ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    created_product = relationship("Product")
    members = relationship("ProductDedupDecisionMember", back_populates="decision", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "(decision_kind = 'merge' AND created_product_id IS NOT NULL) OR (decision_kind <> 'merge' AND created_product_id IS NULL)",
            name="ck_product_dedup_decisions_created_product_merge_only",
        ),
    )


class ProductDedupDecisionMember(Base):
    __tablename__ = "product_dedup_decision_members"

    decision_id = Column(BigInteger, ForeignKey("product_dedup_decisions.id", ondelete="CASCADE"), primary_key=True)
    product_id = Column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)

    decision = relationship("ProductDedupDecision", back_populates="members")
    product = relationship("Product")
