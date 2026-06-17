from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(BigInteger, primary_key=True)
    designer_id = Column(BigInteger, ForeignKey("designers.id", ondelete="SET NULL"), nullable=True, index=True)
    primary_listing_id = Column(BigInteger, ForeignKey("product_listings.id", ondelete="SET NULL"), nullable=True, index=True)
    gender = Column(String(16), nullable=False, default="unisex", server_default="unisex")
    availability_mode = Column(String(16), nullable=False, default="by_order", server_default="by_order")
    manual_weight_grams = Column(Integer, nullable=True)
    weight_rule_id = Column(BigInteger, ForeignKey("weight_rules.id", ondelete="SET NULL"), nullable=True, index=True)
    lifecycle_status = Column(String(16), nullable=False, default="active", server_default="active")
    visibility_status = Column(String(16), nullable=False, default="visible", server_default="visible")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    designer = relationship("Designer")
    primary_listing = relationship("ProductListing", foreign_keys=[primary_listing_id])
    weight_rule = relationship("WeightRule")
    memberships = relationship("ProductListingMember", back_populates="product", cascade="all, delete-orphan")
    presentation = relationship("ProductPresentation", back_populates="product", uselist=False, cascade="all, delete-orphan")
    price_override = relationship("ProductPriceOverride", back_populates="product", uselist=False, cascade="all, delete-orphan")
    gallery_images = relationship("ProductListingGalleryImage", back_populates="product", cascade="all, delete-orphan")


class ProductListing(Base):
    __tablename__ = "product_listings"

    id = Column(BigInteger, primary_key=True)
    source_id = Column(BigInteger, ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False, index=True)
    external_id = Column(String(255), nullable=True)
    url = Column(String(2048), nullable=False)
    handle = Column(String(1024), nullable=True)
    source_title = Column(String(2048), nullable=False)
    source_description_html = Column(Text, nullable=True)
    source_description_text = Column(Text, nullable=True)
    source_weight_grams = Column(Integer, nullable=True)
    source_designer_raw = Column(String(255), nullable=True)
    source_category_raw = Column(String(255), nullable=True)
    orderability_status = Column(String(16), nullable=False, default="orderable", server_default="orderable")
    status_reason = Column(String(255), nullable=True)
    ingest_mode = Column(String(16), nullable=False, default="sync", server_default="sync")
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    source = relationship("Source")
    memberships = relationship("ProductListingMember", back_populates="listing", cascade="all, delete-orphan")
    variants = relationship("ProductListingVariant", back_populates="listing", cascade="all, delete-orphan")
    images = relationship("ProductListingImage", back_populates="listing", cascade="all, delete-orphan")
    gallery_images = relationship("ProductListingGalleryImage", back_populates="listing", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_product_listings_source_external_id"),
        UniqueConstraint("source_id", "url", name="uq_product_listings_source_url"),
        Index("idx_product_listings_last_seen_at", "last_seen_at"),
        Index("idx_product_listings_last_synced_at", "last_synced_at"),
    )


class ProductListingMember(Base):
    __tablename__ = "product_listing_members"

    product_id = Column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    listing_id = Column(BigInteger, ForeignKey("product_listings.id", ondelete="CASCADE"), primary_key=True)

    product = relationship("Product", back_populates="memberships")
    listing = relationship("ProductListing", back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("listing_id", name="uq_product_listing_members_listing_id"),
    )


class ProductListingVariant(Base):
    __tablename__ = "product_listing_variants"

    id = Column(BigInteger, primary_key=True)
    listing_id = Column(BigInteger, ForeignKey("product_listings.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    source_ref_id = Column(String(255), nullable=True)
    sku = Column(String(255), nullable=True)
    title = Column(String(1024), nullable=False)
    price_amount = Column(Numeric(12, 2), nullable=True)
    compare_at_price_amount = Column(Numeric(12, 2), nullable=True)
    currency_code = Column(String(3), nullable=True)
    is_orderable = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    listing = relationship("ProductListing", back_populates="variants")

    __table_args__ = (
        UniqueConstraint("listing_id", "position", name="uq_product_listing_variants_listing_position"),
        Index("idx_product_listing_variants_listing_source_ref_id", "listing_id", "source_ref_id"),
    )


class ProductListingImage(Base):
    __tablename__ = "product_listing_images"

    id = Column(BigInteger, primary_key=True)
    listing_id = Column(BigInteger, ForeignKey("product_listings.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    url = Column(String(2048), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    listing = relationship("ProductListing", back_populates="images")

    __table_args__ = (
        UniqueConstraint("listing_id", "position", name="uq_product_listing_images_listing_position"),
    )


class ProductPresentation(Base):
    __tablename__ = "product_presentation"

    product_id = Column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    title_override = Column(Text, nullable=True)
    description_text = Column(Text, nullable=True)
    description_html = Column(Text, nullable=True)
    description_visibility = Column(Boolean, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    product = relationship("Product", back_populates="presentation")


class ProductPriceOverride(Base):
    __tablename__ = "product_price_overrides"

    product_id = Column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    manual_price_rub = Column(Numeric(12, 2), nullable=False)
    manual_compare_at_price_rub = Column(Numeric(12, 2), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    product = relationship("Product", back_populates="price_override")


class ProductListingGalleryImage(Base):
    __tablename__ = "product_listing_gallery_images"

    id = Column(BigInteger, primary_key=True)
    product_id = Column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    listing_id = Column(BigInteger, ForeignKey("product_listings.id", ondelete="CASCADE"), nullable=False, index=True)
    listing_image_id = Column(BigInteger, ForeignKey("product_listing_images.id", ondelete="SET NULL"), nullable=True)
    image_asset_id = Column(BigInteger, ForeignKey("image_assets.id", ondelete="SET NULL"), nullable=True)
    position = Column(Integer, nullable=False)
    is_hidden = Column(Boolean, nullable=False, default=False, server_default="false")
    origin_kind = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    product = relationship("Product", back_populates="gallery_images")
    listing = relationship("ProductListing", back_populates="gallery_images")
    listing_image = relationship("ProductListingImage")
    image_asset = relationship("ImageAsset")

    __table_args__ = (
        UniqueConstraint("product_id", "listing_id", "position", name="uq_product_listing_gallery_images_scope_position"),
    )
