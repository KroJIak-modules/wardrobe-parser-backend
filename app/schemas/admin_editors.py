from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AdminEditorManualProductRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(gt=0)


class AdminEditorRuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_category_keywords: list[str] = Field(default_factory=list)
    title_keywords: list[str] = Field(default_factory=list)
    manual_products: list[AdminEditorManualProductRef] = Field(default_factory=list)


class AdminEditorFilterNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)
    label: str = Field(min_length=1)
    display_label: str = ""
    mobile_pair_root_id: int | None = Field(default=None, ge=0)
    node_kind: Literal["filter", "multifilter"] = "filter"
    is_enabled: bool = True
    restrict_by_gender: bool = True
    rules: AdminEditorRuleSet
    children: list["AdminEditorFilterNode"] = Field(default_factory=list)


class AdminEditorCategoryAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal["filter", "custom_catalog"]
    ref_id: int = Field(ge=0)
    hidden_node_ids: list[int] = Field(default_factory=list)


class AdminEditorCategoryNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)
    label: str = Field(min_length=1)
    behavior: Literal["new", "designers", "gender", "sale"]
    system_filter_value: str | None = None
    attachments: list[AdminEditorCategoryAttachment] = Field(default_factory=list)
    children: list[dict] = Field(default_factory=list)


class AdminEditorCustomCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)
    label: str = Field(min_length=1)
    description: str = ""
    is_enabled: bool = True
    manual_products: list[AdminEditorManualProductRef] = Field(default_factory=list)


class AdminDesignerEditorSourceRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_brand: str = Field(min_length=1)
    source_product_count: int = Field(ge=0)
    designer_name: str = Field(default="")
    include_in_designers: bool = False


class AdminDesignerEditorDesigner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = ""
    description: str = ""


class AdminDesignerDirectoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = ""
    product_count: int = Field(default=0, ge=0)


class AdminDesignerEditorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[AdminDesignerEditorSourceRow] = Field(default_factory=list)
    designers: list[AdminDesignerEditorDesigner] = Field(default_factory=list)


class AdminFilterAssignmentRebuildStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["idle", "queued", "running"] = "idle"
    target_revision: int = Field(default=0, ge=0)
    applied_revision: int = Field(default=0, ge=0)
    rebuild_total_products: int = Field(default=0, ge=0)
    rebuild_processed_products: int = Field(default=0, ge=0)
    progress_percent: int = Field(default=0, ge=0, le=100)
    rebuild_requested_at: datetime | None = None
    rebuild_started_at: datetime | None = None
    rebuild_completed_at: datetime | None = None
    last_error: str | None = None


class AdminFilterAssignmentRebuildStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    started: bool = False
    status: AdminFilterAssignmentRebuildStatus


class AdminTaxonomyEditorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: list[AdminEditorFilterNode] = Field(default_factory=list)
    categories: list[AdminEditorCategoryNode] = Field(default_factory=list)
    custom_catalogs: list[AdminEditorCustomCatalog] = Field(default_factory=list)
    hidden_product_ids: list[int] = Field(default_factory=list)


AdminEditorFilterNode.model_rebuild()
