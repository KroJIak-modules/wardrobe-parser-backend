"""Schemas for catalog taxonomy state."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TaxonomyFilterNode(BaseModel):
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    title: str = Field(min_length=1)
    display_title: str | None = None
    node_kind: str = Field(default="filter", min_length=1, max_length=16)
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


TaxonomyFilterNode.model_rebuild()
