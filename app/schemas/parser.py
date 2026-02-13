from pydantic import BaseModel, Field


class ParserProductIn(BaseModel):
    site_key: str = Field(..., max_length=64)
    site_name: str | None = Field(default=None, max_length=255)
    site_base_url: str | None = Field(default=None, max_length=512)
    external_id: str | None = Field(default=None, max_length=128)
    name: str
    category: str | None = None
    price: float | None = None
    currency: str | None = None
    size: str | None = None
    additional_info: str | None = None
    size_data: list[dict] | None = None
    image_urls: list[str] | None = None
    product_url: str
    image_url: str | None = None
    description: str | None = None


class ParserBatchIn(BaseModel):
    items: list[ParserProductIn]


class ParserBatchResponse(BaseModel):
    created: int
    updated: int
