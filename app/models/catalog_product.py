from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(BigInteger, primary_key=True)
    designer_id = Column(BigInteger, ForeignKey("designers.id", ondelete="RESTRICT"), nullable=True, index=True)
    primary_listing_id = Column(BigInteger, ForeignKey("product_listings.id", ondelete="SET NULL"), nullable=True, index=True)
    gender = Column(String(16), nullable=False, default="unisex", server_default="unisex")
    source_gender = Column(String(16), nullable=False, default="unisex", server_default="unisex")
    gender_is_manual = Column(Boolean, nullable=False, default=False, server_default="false")
    availability_mode = Column(String(16), nullable=False, default="by_order", server_default="by_order")
    manual_weight_grams = Column(Integer, nullable=True)
    weight_rule_id = Column(BigInteger, ForeignKey("weight_rules.id", ondelete="SET NULL"), nullable=True, index=True)
    lifecycle_status = Column(String(16), nullable=False, default="active", server_default="active")
    dedup_status = Column(String(32), nullable=False, default="independent", server_default="independent")
    dedup_decision_id = Column(
        BigInteger,
        ForeignKey("product_dedup_decisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    dedup_target_product_id = Column(
        BigInteger,
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    visibility_status = Column(String(16), nullable=False, default="visible", server_default="visible")
    is_manually_hidden = Column(Boolean, nullable=False, default=False, server_default="false")
    site_sort_price_rub = Column(Numeric(12, 2), nullable=True, index=True)
    site_sort_price_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    designer = relationship("Designer")
    primary_listing = relationship("ProductListing", foreign_keys=[primary_listing_id])
    weight_rule = relationship("WeightRule")
    memberships = relationship("ProductListingMember", back_populates="product", cascade="all, delete-orphan")
    presentation = relationship("ProductPresentation", back_populates="product", uselist=False, cascade="all, delete-orphan")
    gallery_images = relationship("ProductListingGalleryImage", back_populates="product", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("gender IN ('male', 'female', 'unisex')", name="ck_products_gender"),
        CheckConstraint("availability_mode IN ('in_stock', 'by_order')", name="ck_products_availability_mode"),
        CheckConstraint("lifecycle_status IN ('active')", name="ck_products_lifecycle_status"),
        CheckConstraint(
            "dedup_status IN ('independent', 'combined_source', 'hidden_by_keep')",
            name="ck_products_dedup_status",
        ),
        CheckConstraint("visibility_status IN ('visible', 'hidden')", name="ck_products_visibility_status"),
    )


class ProductListing(Base):
    __tablename__ = "product_listings"

    id = Column(BigInteger, primary_key=True)
    source_id = Column(BigInteger, ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False, index=True)
    external_id = Column(String(255), nullable=True)
    url = Column(String(2048), nullable=False)
    url_normalized = Column(String(2048), nullable=False)
    host_normalized = Column(String(255), nullable=False)
    handle = Column(String(1024), nullable=True)
    source_title = Column(String(2048), nullable=False)
    source_description_html = Column(Text, nullable=True)
    source_description_text = Column(Text, nullable=True)
    source_weight_grams = Column(Integer, nullable=True)
    source_designer_raw = Column(String(255), nullable=True)
    source_category_raw = Column(String(255), nullable=True)
    source_tags = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    source_published_at = Column(DateTime(timezone=True), nullable=True)
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
        CheckConstraint(
            "orderability_status IN ('orderable', 'sold_out', 'unavailable')",
            name="ck_product_listings_orderability_status",
        ),
        CheckConstraint("ingest_mode IN ('sync', 'manual')", name="ck_product_listings_ingest_mode"),
        UniqueConstraint("source_id", "external_id", name="uq_product_listings_source_external_id"),
        UniqueConstraint("source_id", "url", name="uq_product_listings_source_url"),
        Index("idx_product_listings_last_seen_at", "last_seen_at"),
        Index("idx_product_listings_last_synced_at", "last_synced_at"),
        Index("idx_product_listings_source_published_at", "source_published_at"),
        Index("idx_product_listings_url_normalized", "url_normalized"),
        Index("idx_product_listings_host_handle", "host_normalized", "handle"),
    )


class ProductListingMember(Base):
    __tablename__ = "product_listing_members"

    product_id = Column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    listing_id = Column(BigInteger, ForeignKey("product_listings.id", ondelete="CASCADE"), primary_key=True)
    membership_kind = Column(String(16), nullable=False, default="owner", server_default="owner")

    product = relationship("Product", back_populates="memberships")
    listing = relationship("ProductListing", back_populates="memberships")

    __table_args__ = (
        CheckConstraint("membership_kind IN ('owner', 'included')", name="ck_product_listing_members_membership_kind"),
        Index(
            "ux_product_listing_members_owner_listing_id",
            "listing_id",
            unique=True,
            postgresql_where=text("membership_kind = 'owner'"),
        ),
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
    pricing_mode = Column(String(32), nullable=False, default="source", server_default="source")
    is_orderable = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    listing = relationship("ProductListing", back_populates="variants")

    __table_args__ = (
        CheckConstraint("currency_code IS NULL OR char_length(currency_code) = 3", name="ck_product_listing_variants_currency_code"),
        CheckConstraint("pricing_mode IN ('source', 'fixed_final_rub')", name="ck_product_listing_variants_pricing_mode"),
        UniqueConstraint("listing_id", "position", name="uq_product_listing_variants_listing_position"),
        UniqueConstraint("listing_id", "source_ref_id", name="uq_product_listing_variants_listing_source_ref_id"),
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
    brand_override_name = Column(String(255), nullable=True)
    description_text = Column(Text, nullable=True)
    description_html = Column(Text, nullable=True)
    description_visibility = Column(Boolean, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    product = relationship("Product", back_populates="presentation")


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
        CheckConstraint(
            """
            (origin_kind = 'source_image' AND listing_image_id IS NOT NULL AND image_asset_id IS NULL)
            OR
            (origin_kind = 'uploaded_asset' AND listing_image_id IS NULL AND image_asset_id IS NOT NULL)
            """,
            name="ck_product_listing_gallery_images_origin_target",
        ),
    )


class ProductFilterAssignment(Base):
    __tablename__ = "product_filter_assignments"

    product_id = Column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    revision = Column(BigInteger, primary_key=True)
    filter_slug = Column(String(255), nullable=False)
    filter_label = Column(Text, nullable=False)
    manual_rank = Column(Integer, nullable=False, default=0, server_default="0")
    match_score = Column(Integer, nullable=False, default=0, server_default="0")
    matched_local_keywords = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    matched_title_keywords = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    product = relationship("Product")

    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_product_filter_assignments_revision_positive"),
        CheckConstraint("manual_rank IN (0, 1)", name="ck_product_filter_assignments_manual_rank"),
        CheckConstraint("match_score >= 0", name="ck_product_filter_assignments_match_score_non_negative"),
        Index("idx_product_filter_assignments_revision_slug", "revision", "filter_slug", "product_id"),
        Index("idx_product_filter_assignments_revision_product", "revision", "product_id"),
    )


class FilterAssignmentRuntimeState(Base):
    __tablename__ = "filter_assignment_runtime_state"

    id = Column(Integer, primary_key=True)
    target_revision = Column(BigInteger, nullable=False, default=0, server_default="0")
    applied_revision = Column(BigInteger, nullable=False, default=0, server_default="0")
    rebuild_total_products = Column(BigInteger, nullable=False, default=0, server_default="0")
    rebuild_processed_products = Column(BigInteger, nullable=False, default=0, server_default="0")
    rebuild_requested_at = Column(DateTime(timezone=True), nullable=True)
    rebuild_started_at = Column(DateTime(timezone=True), nullable=True)
    rebuild_completed_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("target_revision >= 0", name="ck_filter_assignment_runtime_state_target_non_negative"),
        CheckConstraint("applied_revision >= 0", name="ck_filter_assignment_runtime_state_applied_non_negative"),
        CheckConstraint("rebuild_total_products >= 0", name="ck_filter_assignment_runtime_state_total_non_negative"),
        CheckConstraint("rebuild_processed_products >= 0", name="ck_filter_assignment_runtime_state_processed_non_negative"),
    )
