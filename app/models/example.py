from sqlalchemy import Column, BigInteger, Boolean, DateTime

from app.core.database import Base, utcnow


class Example(Base):
    __tablename__ = "example"

    id = Column(BigInteger, primary_key=True, index=True)
    is_deleted = Column(Boolean, default=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)