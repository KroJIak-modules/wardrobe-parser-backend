from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
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


class Designer(Base):
    __tablename__ = "designers"

    id = Column(BigInteger, primary_key=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    is_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    source_names = relationship("DesignerSourceName", back_populates="designer")


class DesignerSourceName(Base):
    __tablename__ = "designer_source_names"

    id = Column(BigInteger, primary_key=True)
    designer_id = Column(BigInteger, ForeignKey("designers.id", ondelete="SET NULL"), nullable=True, index=True)
    source_name = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    designer = relationship("Designer", back_populates="source_names")


class Source(Base):
    __tablename__ = "sources"

    id = Column(BigInteger, primary_key=True)
    key = Column(String(255), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    base_url = Column(String(2048), nullable=False)
    host_normalized = Column(String(255), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    setting = relationship("SourceSetting", back_populates="source", uselist=False, cascade="all, delete-orphan")
    sync_state = relationship("SourceSyncState", back_populates="source", uselist=False, cascade="all, delete-orphan")


class SourceSetting(Base):
    __tablename__ = "source_settings"

    source_id = Column(BigInteger, ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True)
    supplier_id = Column(BigInteger, ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True, index=True)
    is_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    is_sync_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    hide_auto_added_products = Column(Boolean, nullable=False, default=False, server_default="false")
    description_mode = Column(String(16), nullable=False, default="text", server_default="text")
    show_images = Column(Boolean, nullable=False, default=True, server_default="true")
    promo_factor = Column(Numeric(10, 4), nullable=False, default=1, server_default="1")
    promo_only_no_discount = Column(Boolean, nullable=False, default=False, server_default="false")
    buyout_surcharge_value = Column(Numeric(12, 2), nullable=True)
    buyout_surcharge_currency = Column(String(3), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    source = relationship("Source", back_populates="setting")
    supplier = relationship("Supplier")


class SourceSyncState(Base):
    __tablename__ = "source_sync_state"

    source_id = Column(BigInteger, ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_sync_duration_sec = Column(Integer, nullable=True)
    last_sync_status = Column(String(32), nullable=True)
    last_error_code = Column(String(255), nullable=True)
    last_error_message = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    source = relationship("Source", back_populates="sync_state")


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(BigInteger, primary_key=True)
    key = Column(String(64), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    provider_kind = Column(String(16), nullable=False, default="main", server_default="main")
    parent_supplier_id = Column(BigInteger, ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=True, index=True)
    rate_currency = Column(String(3), nullable=False, default="RUB", server_default="RUB")
    is_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    parent_supplier = relationship("Supplier", remote_side=[id], backref="children")
    shipping_rates = relationship("SupplierShippingRate", back_populates="supplier", cascade="all, delete-orphan")


class SupplierShippingRate(Base):
    __tablename__ = "supplier_shipping_rates"

    id = Column(BigInteger, primary_key=True)
    supplier_id = Column(BigInteger, ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False, index=True)
    min_weight_kg = Column(Numeric(10, 3), nullable=False)
    max_weight_kg = Column(Numeric(10, 3), nullable=True)
    price_rub = Column(Numeric(12, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    supplier = relationship("Supplier", back_populates="shipping_rates")


class PricingSetting(Base):
    __tablename__ = "pricing_settings"

    id = Column(BigInteger, primary_key=True)
    markup_multiplier = Column(Numeric(10, 4), nullable=False, default=1, server_default="1")
    weight_tolerance = Column(Numeric(10, 4), nullable=False, default=1, server_default="1")
    customs_threshold_eur = Column(Numeric(12, 2), nullable=False, default=200, server_default="200")
    customs_duty_rate = Column(Numeric(10, 4), nullable=False, default=0.15, server_default="0.15")
    eur_to_rub_rate = Column(Numeric(12, 4), nullable=False, default=105, server_default="105")
    usd_to_rub_rate = Column(Numeric(12, 4), nullable=False, default=95, server_default="95")
    usdt_to_rub_rate = Column(Numeric(12, 4), nullable=False, default=95, server_default="95")
    usdt_extra_rub = Column(Numeric(12, 4), nullable=False, default=1, server_default="1")
    payment_fee_rate = Column(Numeric(10, 4), nullable=False, default=0.02, server_default="0.02")
    customs_processing_rate = Column(Numeric(10, 4), nullable=False, default=0.08, server_default="0.08")
    customs_fixed_rub = Column(Numeric(12, 2), nullable=False, default=540, server_default="540")
    tax_rate = Column(Numeric(10, 4), nullable=False, default=0.06, server_default="0.06")
    final_rounding_mode = Column(String(32), nullable=False, default="unit", server_default="unit")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class WeightRule(Base):
    __tablename__ = "weight_rules"

    id = Column(BigInteger, primary_key=True)
    weight_grams = Column(Integer, nullable=False)
    is_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class WeightRuleKeyword(Base):
    __tablename__ = "weight_rule_keywords"

    id = Column(BigInteger, primary_key=True)
    rule_id = Column(BigInteger, ForeignKey("weight_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    keyword = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    rule = relationship("WeightRule")

    __table_args__ = (
        UniqueConstraint("rule_id", "keyword", name="uq_weight_rule_keyword"),
    )


class ImageAsset(Base):
    __tablename__ = "image_assets"

    id = Column(BigInteger, primary_key=True)
    storage_key = Column(String(2048), nullable=False)
    mime_type = Column(String(255), nullable=False)
    byte_size = Column(BigInteger, nullable=False)
    width_px = Column(Integer, nullable=True)
    height_px = Column(Integer, nullable=True)
    checksum_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("checksum_sha256", name="uq_image_assets_checksum_sha256"),
    )


class ShowcaseSetting(Base):
    __tablename__ = "showcase_settings"

    id = Column(BigInteger, primary_key=True)
    hero_image_asset_id = Column(BigInteger, ForeignKey("image_assets.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    hero_image_asset = relationship("ImageAsset")


class ShowcaseCarouselImage(Base):
    __tablename__ = "showcase_carousel_images"

    id = Column(BigInteger, primary_key=True)
    image_asset_id = Column(BigInteger, ForeignKey("image_assets.id", ondelete="CASCADE"), nullable=False)
    position = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    image_asset = relationship("ImageAsset")

    __table_args__ = (
        UniqueConstraint("position", name="uq_showcase_carousel_images_position"),
    )


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id = Column(BigInteger, primary_key=True)
    trigger_kind = Column(String(16), nullable=False)
    status = Column(String(32), nullable=False)
    triggered_by_admin_user_id = Column(Integer, ForeignKey("admin_user.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    total_sources = Column(Integer, nullable=False, default=0, server_default="0")
    processed_sources = Column(Integer, nullable=False, default=0, server_default="0")
    products_seen = Column(Integer, nullable=False, default=0, server_default="0")
    products_applied = Column(Integer, nullable=False, default=0, server_default="0")
    error_message = Column(Text, nullable=True)

    triggered_by_admin_user = relationship("AdminUser")


class SyncJobSourceRun(Base):
    __tablename__ = "sync_job_source_runs"

    id = Column(BigInteger, primary_key=True)
    sync_job_id = Column(BigInteger, ForeignKey("sync_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    source_id = Column(BigInteger, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(32), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    products_received = Column(Integer, nullable=False, default=0, server_default="0")
    products_applied = Column(Integer, nullable=False, default=0, server_default="0")
    failed_products = Column(Integer, nullable=False, default=0, server_default="0")
    error_code = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)

    sync_job = relationship("SyncJob")
    source = relationship("Source")


class SyncAppliedBatch(Base):
    __tablename__ = "sync_applied_batches"

    id = Column(BigInteger, primary_key=True)
    source_run_id = Column(BigInteger, ForeignKey("sync_job_source_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_key = Column(String(255), nullable=False)
    applied_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    source_run = relationship("SyncJobSourceRun")

    __table_args__ = (
        UniqueConstraint("source_run_id", "batch_key", name="uq_sync_applied_batches_source_run_batch_key"),
        Index("idx_sync_applied_batches_applied_at", "applied_at"),
    )
