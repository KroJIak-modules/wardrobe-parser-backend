"""Schemas for catalog taxonomy state."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TaxonomyFilterNode(BaseModel):
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    title: str = Field(min_length=1)
    display_title: str | None = None
    mobile_pair_slug: str | None = Field(default=None, min_length=1, max_length=255)
    default_weight_rule_id: int | None = Field(default=None, ge=1)
    node_kind: Literal["filter", "multifilter"] = "filter"
    is_enabled: bool = True
    local_category_keywords: list[str] = Field(default_factory=list)
    title_keywords: list[str] = Field(default_factory=list)
    manual_product_ids: list[int] = Field(default_factory=list)
    children: list["TaxonomyFilterNode"] = Field(default_factory=list)


class TaxonomyCustomCatalog(BaseModel):
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    title: str = Field(min_length=1)
    description: str | None = None
    is_enabled: bool = True
    product_ids: list[int] = Field(default_factory=list)


class TaxonomyShowcaseAttachment(BaseModel):
    kind: Literal["filter", "custom_catalog"]
    filter_slug: str | None = Field(default=None, min_length=1, max_length=255)
    custom_catalog_slug: str | None = Field(default=None, min_length=1, max_length=255)
    hidden_filter_slugs: list[str] = Field(default_factory=list)


class TaxonomyShowcaseCategory(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1)
    attachments: list[TaxonomyShowcaseAttachment] = Field(default_factory=list)


class TaxonomyState(BaseModel):
    filters: list[TaxonomyFilterNode] = Field(default_factory=list)
    custom_catalogs: list[TaxonomyCustomCatalog] = Field(default_factory=list)
    showcase_categories: list[TaxonomyShowcaseCategory] = Field(default_factory=list)


class TaxonomyFilterWriteNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref_slug: str | None = Field(default=None, min_length=1, max_length=255)
    title: str = Field(min_length=1)
    display_title: str | None = None
    mobile_pair_slug: str | None = Field(default=None, min_length=1, max_length=255)
    default_weight_rule_id: int | None = Field(default=None, ge=1)
    node_kind: Literal["filter", "multifilter"] = "filter"
    is_enabled: bool = True
    local_category_keywords: list[str] = Field(default_factory=list)
    title_keywords: list[str] = Field(default_factory=list)
    manual_product_ids: list[int] = Field(default_factory=list)
    children: list["TaxonomyFilterWriteNode"] = Field(default_factory=list)


class TaxonomyCustomCatalogWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref_slug: str | None = Field(default=None, min_length=1, max_length=255)
    title: str = Field(min_length=1)
    description: str | None = None
    is_enabled: bool = True
    product_ids: list[int] = Field(default_factory=list)


class TaxonomyShowcaseAttachmentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["filter", "custom_catalog"]
    filter_slug: str | None = Field(default=None, min_length=1, max_length=255)
    custom_catalog_slug: str | None = Field(default=None, min_length=1, max_length=255)
    hidden_filter_slugs: list[str] = Field(default_factory=list)


class TaxonomyShowcaseCategoryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1)
    attachments: list[TaxonomyShowcaseAttachmentWrite] = Field(default_factory=list)


class TaxonomyWriteState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: list[TaxonomyFilterWriteNode] = Field(default_factory=list)
    custom_catalogs: list[TaxonomyCustomCatalogWrite] = Field(default_factory=list)
    showcase_categories: list[TaxonomyShowcaseCategoryWrite] = Field(default_factory=list)


TaxonomyFilterNode.model_rebuild()
TaxonomyFilterWriteNode.model_rebuild()
