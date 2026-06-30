from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ShowcaseViewport = Literal["desktop", "mobile"]
ShowcaseMediaKind = Literal["image", "video"]


class ShowcaseMediaAssetResponse(BaseModel):
    id: int = Field(ge=1)
    mime_type: str = Field(min_length=1, max_length=255)
    media_kind: ShowcaseMediaKind
    byte_size: int = Field(ge=0)
    width_px: int | None = Field(default=None, ge=1, le=20000)
    height_px: int | None = Field(default=None, ge=1, le=20000)


class ShowcaseViewportStateResponse(BaseModel):
    hero_asset: ShowcaseMediaAssetResponse | None = None
    carousel_assets: list[ShowcaseMediaAssetResponse] = Field(default_factory=list)


class ShowcaseStateResponse(BaseModel):
    desktop: ShowcaseViewportStateResponse
    mobile: ShowcaseViewportStateResponse
    carousel_limit: int = Field(default=20, ge=1, le=1000)


class ShowcaseViewportStateUpdateRequest(BaseModel):
    hero_asset_id: int | None = Field(default=None, ge=1)
    carousel_asset_ids: list[int] = Field(default_factory=list)


class ShowcaseStateUpdateRequest(BaseModel):
    desktop: ShowcaseViewportStateUpdateRequest = Field(default_factory=ShowcaseViewportStateUpdateRequest)
    mobile: ShowcaseViewportStateUpdateRequest = Field(default_factory=ShowcaseViewportStateUpdateRequest)


class ShowcaseMediaUploadResponse(BaseModel):
    ok: bool = True
    asset: ShowcaseMediaAssetResponse
