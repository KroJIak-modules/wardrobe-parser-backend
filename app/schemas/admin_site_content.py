from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.site import SiteMediaAssetResponse


class AdminSiteAboutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    photos: list[SiteMediaAssetResponse] = Field(default_factory=list)


class AdminSiteAboutUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = ""
    photo_asset_ids: list[int] = Field(default_factory=list)


class AdminSiteQuestionItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    question: str
    answer: str
    is_enabled: bool
    is_expanded_by_default: bool
    position: int


class AdminSiteQuestionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdminSiteQuestionItemResponse] = Field(default_factory=list)


class AdminSiteQuestionWriteItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    question: str = ""
    answer: str = ""
    is_enabled: bool = True
    is_expanded_by_default: bool = False


class AdminSiteQuestionsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdminSiteQuestionWriteItem] = Field(default_factory=list)


class AdminSiteNotificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    version: int
    title: str
    description: str
    button_text: str
    button_url: str
    image: SiteMediaAssetResponse | None = None
    created_at: str
    updated_at: str


class AdminSiteNotificationsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdminSiteNotificationResponse] = Field(default_factory=list)


class AdminSiteNotificationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    description: str = ""
    button_text: str = ""
    button_url: str = ""
    image_asset_id: int | None = Field(default=None, ge=1)


class AdminSiteContentMediaUploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    asset: SiteMediaAssetResponse


class AdminSiteAccessSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    title: str = ""
    description: str = ""
    password: str = ""
    updated_at: str = ""


class AdminSiteAccessSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    title: str = ""
    description: str = ""
    password: str = ""


class AdminSiteAccessPasswordGenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str
