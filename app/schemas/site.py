from datetime import datetime

from pydantic import BaseModel, Field


class SiteBase(BaseModel):
    key: str = Field(..., max_length=64)
    name: str = Field(..., max_length=255)
    base_url: str | None = Field(default=None, max_length=512)
    is_active: bool = True


class SiteCreate(SiteBase):
    pass


class SiteUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    base_url: str | None = Field(default=None, max_length=512)
    is_active: bool | None = None


class SiteStatusUpdate(BaseModel):
    key: str
    name: str | None = None
    base_url: str | None = None
    is_active: bool | None = None
    last_status: str | None = None
    last_status_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None


class SiteResponse(SiteBase):
    id: int
    last_status: str | None = None
    last_status_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None

    class Config:
        from_attributes = True
