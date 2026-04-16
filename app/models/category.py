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
    is_enabled = Column(Boolean, nullable=False, default=True)

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
        Index("idx_parser_category_is_enabled", "is_enabled"),
    )


class ParserCategoryKeyword(Base):
    """Keyword-to-category mapping for categorization rules."""

    __tablename__ = "parser_category_keyword"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("parser_category.id", ondelete="CASCADE"), nullable=False)
    keyword = Column(String(255), nullable=False)
    keyword_scope = Column(String(16), nullable=False, default="local", server_default="local")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    category = relationship("ParserCategory", back_populates="keywords")

    __table_args__ = (
        UniqueConstraint("category_id", "keyword", "keyword_scope", name="uq_parser_category_keyword_scope"),
        Index("idx_parser_category_keyword_category", "category_id"),
        Index("idx_parser_category_keyword_keyword", "keyword"),
        Index("idx_parser_category_keyword_scope", "keyword_scope"),
    )


class ParserCategoryManualProduct(Base):
    """Manual product-to-category assignments."""

    __tablename__ = "parser_category_manual_product"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("parser_category.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("parser_product.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("category_id", "product_id", name="uq_parser_category_manual_product"),
        Index("idx_parser_category_manual_category", "category_id"),
        Index("idx_parser_category_manual_product", "product_id"),
    )


class ParserProductCategoryMatch(Base):
    """Resolved product-category matches (manual and auto)."""

    __tablename__ = "parser_product_category_match"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("parser_product.id", ondelete="CASCADE"), nullable=False)
    category_id = Column(Integer, ForeignKey("parser_category.id", ondelete="CASCADE"), nullable=False)
    match_source = Column(String(16), nullable=False, default="auto", server_default="auto")
    score = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("product_id", "category_id", "match_source", name="uq_parser_product_category_match"),
        Index("idx_parser_product_category_match_product", "product_id"),
        Index("idx_parser_product_category_match_category", "category_id"),
        Index("idx_parser_product_category_match_source", "match_source"),
    )


class ParserCategoryCountSnapshot(Base):
    """Precomputed direct and subtree counters for category tree."""

    __tablename__ = "parser_category_count_snapshot"

    category_id = Column(Integer, ForeignKey("parser_category.id", ondelete="CASCADE"), primary_key=True)
    direct_count = Column(Integer, nullable=False, default=0, server_default="0")
    subtree_count = Column(Integer, nullable=False, default=0, server_default="0")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ParserCategoryIndexState(Base):
    """Technical state for category index rebuilds."""

    __tablename__ = "parser_category_index_state"

    id = Column(Integer, primary_key=True)
    matches_built_at = Column(DateTime(timezone=True), nullable=True)
    counts_built_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
