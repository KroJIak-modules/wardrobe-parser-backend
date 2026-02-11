from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("site_id", "external_id", name="uq_products_site_external"),)

    id = Column(BigInteger, primary_key=True, index=True)
    site_id = Column(BigInteger, ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, index=True)
    external_id = Column(String(128), nullable=False, index=True)
    name = Column(String(512), nullable=False)
    category = Column(String(255), nullable=True)
    price = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(16), nullable=True)
    product_url = Column(String(1024), nullable=False)
    image_url = Column(String(1024), nullable=True)
    description = Column(Text, nullable=True)
    raw_data = Column(JSONB, nullable=True)
    parser_updated_at = Column(DateTime(timezone=True), nullable=True)
    user_updated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    is_deleted = Column(Boolean, nullable=False, server_default="false")
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    site = relationship("Site", backref="products")
