from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class ProductImage(Base):
    __tablename__ = "product_images"
    __table_args__ = (
        UniqueConstraint("product_id", "url", name="uq_product_images_product_url"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    product_id = Column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(String(1024), nullable=False)
    sort_order = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    is_deleted = Column(Boolean, nullable=False, server_default="false")
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    product = relationship("Product", backref="images")
