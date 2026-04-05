"""Pricing and supplier models for final customer price formula."""

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class ParserSupplier(Base):
    """Supplier (country) used for shipping tariff mapping."""

    __tablename__ = "parser_supplier"

    id = Column(Integer, primary_key=True)
    key = Column(String(64), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    country_code = Column(String(16), nullable=False, default="N/A")
    country_name = Column(String(255), nullable=False, default="Unknown")
    rate_currency = Column(String(3), nullable=False, default="RUB")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    sources = relationship("ParserSource", back_populates="supplier")
    shipping_rates = relationship(
        "ParserSupplierShippingRate",
        back_populates="supplier",
        order_by="ParserSupplierShippingRate.step_500g.asc()",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_parser_supplier_key", "key"),
        Index("idx_parser_supplier_country_code", "country_code"),
    )


class ParserSupplierShippingRate(Base):
    """SSR table entry for one supplier and one 500g step."""

    __tablename__ = "parser_supplier_shipping_rate"

    id = Column(Integer, primary_key=True)
    supplier_id = Column(Integer, ForeignKey("parser_supplier.id", ondelete="CASCADE"), nullable=False)
    step_500g = Column(Integer, nullable=False)
    rate_rub = Column(Float, nullable=False, default=0.0)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    supplier = relationship("ParserSupplier", back_populates="shipping_rates")

    __table_args__ = (
        UniqueConstraint("supplier_id", "step_500g", name="uq_parser_supplier_shipping_rate_supplier_step"),
        Index("idx_parser_supplier_shipping_rate_supplier_id", "supplier_id"),
        Index("idx_parser_supplier_shipping_rate_step_500g", "step_500g"),
    )


class ParserPricingSettings(Base):
    """Singleton-like row with configurable pricing formula parameters."""

    __tablename__ = "parser_pricing_settings"

    id = Column(Integer, primary_key=True)
    markup_multiplier = Column(Float, nullable=False, default=1.0)
    weight_tolerance = Column(Float, nullable=False, default=1.0)
    promo_factor = Column(Float, nullable=False, default=1.0)
    customs_threshold_eur = Column(Float, nullable=False, default=200.0)
    customs_threshold_currency = Column(String(3), nullable=False, default="EUR")
    customs_duty_rate = Column(Float, nullable=False, default=0.15)
    seller_delivery_rub = Column(Float, nullable=False, default=0.0)
    usd_to_rub = Column(Float, nullable=False, default=95.0)
    eur_to_rub = Column(Float, nullable=False, default=105.0)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_parser_pricing_settings_updated_at", "updated_at"),
    )
