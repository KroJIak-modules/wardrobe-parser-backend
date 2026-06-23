from __future__ import annotations

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
    node_kind: Literal["filter", "multifilter"] = "filter"
    is_enabled: bool = True
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
    logo_image_asset_id: int | None = None


class AdminDesignerEditorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[AdminDesignerEditorSourceRow] = Field(default_factory=list)
    designers: list[AdminDesignerEditorDesigner] = Field(default_factory=list)


class AdminTaxonomyEditorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: list[AdminEditorFilterNode] = Field(default_factory=list)
    categories: list[AdminEditorCategoryNode] = Field(default_factory=list)
    custom_catalogs: list[AdminEditorCustomCatalog] = Field(default_factory=list)
    hidden_product_ids: list[int] = Field(default_factory=list)


AdminEditorFilterNode.model_rebuild()
