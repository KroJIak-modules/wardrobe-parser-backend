"""Category tree and keyword rules for parser catalog."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class ParserCategory(Base):
    """Category node with parent-child hierarchy."""

    __tablename__ = "parser_category"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False)
    parent_id = Column(Integer, ForeignKey("parser_category.id"), nullable=True)
    is_fallback = Column(Boolean, nullable=False, default=False)
    is_favorite = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    parent = relationship("ParserCategory", remote_side=[id], back_populates="children")
    children = relationship("ParserCategory", back_populates="parent")
    keywords = relationship("ParserCategoryKeyword", back_populates="category", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("slug", name="uq_parser_category_slug"),
        Index("idx_parser_category_parent_id", "parent_id"),
        Index("idx_parser_category_deleted_at", "deleted_at"),
        Index("idx_parser_category_is_fallback", "is_fallback"),
        Index("idx_parser_category_is_favorite", "is_favorite"),
    )


class ParserCategoryKeyword(Base):
    """Keyword-to-category mapping for categorization rules."""

    __tablename__ = "parser_category_keyword"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("parser_category.id", ondelete="CASCADE"), nullable=False)
    keyword = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    category = relationship("ParserCategory", back_populates="keywords")

    __table_args__ = (
        UniqueConstraint("category_id", "keyword", name="uq_parser_category_keyword"),
        Index("idx_parser_category_keyword_category", "category_id"),
        Index("idx_parser_category_keyword_keyword", "keyword"),
    )
