from datetime import datetime

from pydantic import BaseModel, Field


class ProductBase(BaseModel):
    site_id: int
    external_id: str = Field(..., max_length=128)
    name: str = Field(..., max_length=512)
    category: str | None = Field(default=None, max_length=255)
    price: float | None = None
    currency: str | None = Field(default=None, max_length=16)
    product_url: str = Field(..., max_length=1024)
    image_url: str | None = Field(default=None, max_length=1024)
    description: str | None = None


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=512)
    category: str | None = Field(default=None, max_length=255)
    price: float | None = None
    currency: str | None = Field(default=None, max_length=16)
    product_url: str | None = Field(default=None, max_length=1024)
    image_url: str | None = Field(default=None, max_length=1024)
    description: str | None = None


class ProductResponse(ProductBase):
    id: int
    parser_updated_at: datetime | None = None
    user_updated_at: datetime | None = None

    class Config:
        from_attributes = True
