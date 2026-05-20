"""Core parser entities mirrored in backend for native business endpoints."""

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
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
    hide_auto_added_products = Column(Boolean, nullable=False, default=False)
    show_description = Column(Boolean, nullable=False, default=True)
    show_images = Column(Boolean, nullable=False, default=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_sync_duration_sec = Column(Integer, nullable=True)
    last_sync_status = Column(String(32), nullable=True)
    supplier_id = Column(Integer, ForeignKey("parser_supplier.id", ondelete="RESTRICT"), nullable=False)
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
    source_external_id = Column(String(255), nullable=True)
    canonical_url = Column(String(2048), nullable=True)
    handle = Column(String(1024), nullable=False)
    title = Column(String(2048), nullable=False)
    description = Column(Text, nullable=True)
    vendor = Column(String(255), nullable=True)
    product_type = Column(String(255), nullable=True)
    url = Column(String(2048), nullable=False)
    price = Column(Float, nullable=True)
    status = Column(
        PGEnum(
            "available",
            "out_of_stock",
            "hidden",
            "unavailable",
            name="productstatus",
            create_type=False,
        ),
        nullable=False,
        default="available",
    )
    image_count = Column(Integer, nullable=False, default=0)
    image_urls = Column(JSON, nullable=False, default=list)
    image_asset_ids = Column(JSON, nullable=False, default=list)
    title_override = Column(Text, nullable=True)
    description_override = Column(Text, nullable=True)
    title_sync_locked = Column(Boolean, nullable=False, default=False)
    description_sync_locked = Column(Boolean, nullable=False, default=False)
    description_visible_override = Column(Boolean, nullable=True)
    images_sync_locked = Column(Boolean, nullable=False, default=False)
    hidden_source_image_asset_ids = Column(JSON, nullable=False, default=list)
    manual_image_asset_ids = Column(JSON, nullable=False, default=list)
    manual_image_order = Column(JSON, nullable=False, default=list)
    variants = Column(JSON, nullable=False, default=list)
    is_auto_added = Column(Boolean, nullable=False, default=True)
    auto_hide_force_visible = Column(Boolean, nullable=False, default=False)
    weight_grams = Column(Float, nullable=True)
    weight_source = Column(String(32), nullable=True)
    weight_match_keyword = Column(String(255), nullable=True)
    weight_value = Column(Float, nullable=True)
    weight_unit = Column(String(16), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    source = relationship("ParserSource", back_populates="products")
    origin_variants = relationship(
        "ParserProductOriginVariant",
        back_populates="product",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_parser_product_source_external_id", "source_id", "source_external_id"),
        Index("idx_parser_product_source_canonical_url", "source_id", "canonical_url"),
    )


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


class ParserBrandMapping(Base):
    """Manual mapping of original brand names to canonical display brand names."""

    __tablename__ = "parser_brand_mapping"

    id = Column(Integer, primary_key=True)
    source_brand = Column(String(255), nullable=False)
    source_brand_key = Column(String(255), nullable=False, unique=True)
    target_brand = Column(String(255), nullable=False)
    include_in_designers = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_parser_brand_mapping_source_brand", "source_brand"),
    )


class ParserProductOriginVariant(Base):
    """Variant-level source lineage for multi-source product aggregation."""

    __tablename__ = "parser_product_origin_variant"

    id = Column(Integer, primary_key=True)
    origin_key = Column(String(512), nullable=False, unique=True)
    product_id = Column(Integer, ForeignKey("parser_product.id", ondelete="CASCADE"), nullable=False)
    source_id = Column(Integer, ForeignKey("parser_source.id", ondelete="RESTRICT"), nullable=False)
    source_product_url = Column(String(2048), nullable=False)
    source_variant_id = Column(String(255), nullable=True)
    source_variant_title = Column(String(1024), nullable=True)
    sku = Column(String(255), nullable=True)
    price = Column(Float, nullable=True)
    currency = Column(String(3), nullable=True)
    available = Column(Boolean, nullable=False, default=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    product = relationship("ParserProduct", back_populates="origin_variants")
    source = relationship("ParserSource")

    __table_args__ = (
        Index("idx_parser_origin_variant_product_id", "product_id"),
        Index("idx_parser_origin_variant_source_id", "source_id"),
    )
