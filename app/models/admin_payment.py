from sqlalchemy import BigInteger, Column, DateTime, Index, Numeric, String, Text, func

from app.core.database import Base


class AdminPayment(Base):
    __tablename__ = "admin_payment"

    id = Column(BigInteger, primary_key=True)
    order_number = Column(String(100), nullable=False, unique=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    title = Column(String(100), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    description = Column(Text, nullable=False)
    email = Column(String(320))
    phone = Column(String(40))
    customer_name = Column(String(255))
    address = Column(Text)
    status = Column(String(32), nullable=False)
    payment_url = Column(Text)
    provider_payment_id = Column(String(255))
    ownership_token = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_admin_payment_created_id", "created_at", "id"),)
