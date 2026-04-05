"""Core parser entities mirrored in backend for native business endpoints."""

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class ParserSource(Base):
    """Parser source (Shopify store)."""

    __tablename__ = "parser_source"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    url = Column(String(2048), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    supplier_id = Column(Integer, ForeignKey("parser_supplier.id", ondelete="RESTRICT"), nullable=False)
    seller_delivery_rub = Column(Float, nullable=False, default=0.0)
    promo_factor = Column(Float, nullable=False, default=1.0)
    promo_only_no_discount = Column(Boolean, nullable=False, default=False)
    buyout_surcharge_value = Column(Float, nullable=False, default=0.0)
    buyout_surcharge_currency = Column(String(3), nullable=False, default="RUB")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    supplier = relationship("ParserSupplier", back_populates="sources")
    products = relationship("ParserProduct", back_populates="source")


class ParserProduct(Base):
    """Parsed product used by settings/category flows."""

    __tablename__ = "parser_product"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("parser_source.id"), nullable=False)
    handle = Column(String(1024), nullable=False)
    title = Column(String(2048), nullable=False)
    vendor = Column(String(255), nullable=True)
    product_type = Column(String(255), nullable=True)
    url = Column(String(2048), nullable=False)
    price = Column(Float, nullable=True)
    currency = Column(String(3), nullable=False, default="USD")
    status = Column(String(32), nullable=False, default="available")
    image_count = Column(Integer, nullable=False, default=0)
    image_urls = Column(JSON, nullable=False, default=list)
    image_asset_ids = Column(JSON, nullable=False, default=list)
    variants = Column(JSON, nullable=False, default=list)
    weight_grams = Column(Float, nullable=True)
    weight_source = Column(String(32), nullable=True)
    weight_match_keyword = Column(String(255), nullable=True)
    weight_value = Column(Float, nullable=True)
    weight_unit = Column(String(16), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    source = relationship("ParserSource", back_populates="products")


class ParserFavoriteProduct(Base):
    """Manual 'favorite' mark for parser products."""

    __tablename__ = "parser_favorite_product"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("parser_product.id", ondelete="CASCADE"), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    product = relationship("ParserProduct")

    __table_args__ = (
        Index("idx_parser_favorite_product_product_id", "product_id"),
    )


class ParserDedupDecision(Base):
    """Stored moderator decision for a pair of products."""

    __tablename__ = "parser_dedup_decision"

    id = Column(Integer, primary_key=True)
    pair_key = Column(String(64), nullable=False, unique=True)
    left_product_id = Column(Integer, ForeignKey("parser_product.id"), nullable=False)
    right_product_id = Column(Integer, ForeignKey("parser_product.id"), nullable=False)
    action = Column(String(20), nullable=False)
    merged_into_product_id = Column(Integer, ForeignKey("parser_product.id"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_parser_dedup_decision_action", "action"),
        Index("idx_parser_dedup_decision_left", "left_product_id"),
        Index("idx_parser_dedup_decision_right", "right_product_id"),
    )
