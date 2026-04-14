"""Pricing and supplier models for final customer price formula."""

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


def _default_insurance_rules() -> list[dict]:
    return [
        {"min_eur": 0.0, "max_eur": 300.0, "mode": "percent", "value": 0.01},
        {"min_eur": 300.0, "max_eur": 520.0, "mode": "fixed_rub", "value": 1000.0},
        {"min_eur": 520.0, "max_eur": None, "mode": "fixed_rub", "value": 1300.0},
    ]


def _default_service_fee_rules() -> list[dict]:
    return [
        {"min_rub": 0.0, "max_rub": 7000.0, "mode": "percent", "value": 0.25},
        {"min_rub": 7000.0, "max_rub": 10000.0, "mode": "fixed_rub", "value": 2500.0},
        {"min_rub": 10000.0, "max_rub": 17000.0, "mode": "fixed_rub", "value": 3000.0},
        {"min_rub": 17000.0, "max_rub": 20000.0, "mode": "fixed_rub", "value": 3500.0},
        {"min_rub": 20000.0, "max_rub": 30000.0, "mode": "percent", "value": 0.20},
        {"min_rub": 30000.0, "max_rub": 40000.0, "mode": "fixed_rub", "value": 6000.0},
        {"min_rub": 40000.0, "max_rub": None, "mode": "percent", "value": 0.15},
    ]


def _default_shipping_rules() -> dict:
    return {
        "US": {
            "normal": [
                {"kg": 0.5, "rub": 1400.0},
                {"kg": 1.0, "rub": 1650.0},
                {"kg": 1.5, "rub": 2250.0},
                {"kg": 2.0, "rub": 2900.0},
                {"kg": 2.5, "rub": 3500.0},
                {"kg": 3.0, "rub": 4100.0},
            ],
            "alt": [
                {"kg": 0.5, "rub": 1700.0},
                {"kg": 1.0, "rub": 3350.0},
                {"kg": 1.5, "rub": 4100.0},
                {"kg": 2.0, "rub": 4950.0},
                {"kg": 2.5, "rub": 5650.0},
                {"kg": 3.0, "rub": 6500.0},
            ],
        },
        "EU": {
            "normal": [
                {"kg": 0.5, "rub": 1100.0},
                {"kg": 1.0, "rub": 1500.0},
                {"kg": 1.5, "rub": 1900.0},
                {"kg": 2.0, "rub": 2300.0},
                {"kg": 2.5, "rub": 2700.0},
                {"kg": 3.0, "rub": 3150.0},
            ],
            "alt": [
                {"kg": 0.5, "rub": 2300.0},
                {"kg": 1.0, "rub": 2750.0},
                {"kg": 1.5, "rub": 3750.0},
                {"kg": 2.0, "rub": 4800.0},
                {"kg": 2.5, "rub": 5800.0},
                {"kg": 3.0, "rub": 6800.0},
            ],
        },
        "UK": {
            "normal": [
                {"kg": 0.5, "rub": 3400.0},
                {"kg": 1.0, "rub": 3900.0},
                {"kg": 1.5, "rub": 4400.0},
                {"kg": 2.0, "rub": 4900.0},
                {"kg": 2.5, "rub": 5450.0},
                {"kg": 3.0, "rub": 5950.0},
            ],
            "alt": [],
        },
    }


class ParserSupplier(Base):
    """Supplier (country) used for shipping tariff mapping."""

    __tablename__ = "parser_supplier"

    id = Column(Integer, primary_key=True)
    key = Column(String(64), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    category = Column(String(16), nullable=False, default="main")
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
        Index("idx_parser_supplier_category", "category"),
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
    usd_to_rub = Column(Float, nullable=False, default=95.0)
    eur_to_rub = Column(Float, nullable=False, default=105.0)
    bybit_usdt_to_rub = Column(Float, nullable=False, default=95.0)
    bybit_extra_rub = Column(Float, nullable=False, default=1.0)
    final_rounding_mode = Column(String(32), nullable=False, default="unit")
    bybit_bucket_rates = Column(JSON, nullable=False, default=list)
    bybit_last_updated_at = Column(DateTime(timezone=True), nullable=True)
    bybit_last_error = Column(String(1024), nullable=True)
    eur_to_usd_rate = Column(Float, nullable=False, default=1.18)
    gbp_to_usd_rate = Column(Float, nullable=False, default=1.4)
    payment_fee_rate = Column(Float, nullable=False, default=0.02)
    customs_processing_rate = Column(Float, nullable=False, default=0.08)
    customs_fixed_rub = Column(Float, nullable=False, default=540.0)
    shipping_alt_threshold_eur = Column(Float, nullable=False, default=300.0)
    tax_rate = Column(Float, nullable=False, default=0.06)
    svc_rules = Column(JSON, nullable=False, default=list)
    insurance_rules = Column(JSON, nullable=False, default=_default_insurance_rules)
    service_fee_rules = Column(JSON, nullable=False, default=_default_service_fee_rules)
    shipping_rules = Column(JSON, nullable=False, default=_default_shipping_rules)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_parser_pricing_settings_updated_at", "updated_at"),
    )
