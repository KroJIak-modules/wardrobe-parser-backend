"""Weight rules and keyword mappings for estimated product weight."""

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class ParserWeightRule(Base):
    """One weight bucket with associated keywords."""

    __tablename__ = "parser_weight_rule"

    id = Column(Integer, primary_key=True)
    weight_grams = Column(Integer, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    keywords = relationship("ParserWeightKeyword", back_populates="rule", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_parser_weight_rule_deleted_at", "deleted_at"),
        Index("idx_parser_weight_rule_weight_grams", "weight_grams"),
    )


class ParserWeightKeyword(Base):
    """Keyword entry linked to one rule."""

    __tablename__ = "parser_weight_keyword"

    id = Column(Integer, primary_key=True)
    rule_id = Column(Integer, ForeignKey("parser_weight_rule.id", ondelete="CASCADE"), nullable=False)
    keyword = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    rule = relationship("ParserWeightRule", back_populates="keywords")

    __table_args__ = (
        UniqueConstraint("rule_id", "keyword", name="uq_parser_weight_rule_keyword"),
        Index("idx_parser_weight_keyword_rule_id", "rule_id"),
        Index("idx_parser_weight_keyword_keyword", "keyword"),
    )
