from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ShowcaseRouteTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pathname: Literal["/", "/catalog", "/catalog/designers", "/catalog/sale", "/designers"]
    query: dict[str, str | list[str]] | None = None


class ShowcaseNavigationMenuItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["curated_listing", "system_link", "filter_link", "filter_bundle"]
    label: str
    target: ShowcaseRouteTarget
    presentation: Literal["default", "heading"] | None = None


class ShowcaseNavigationMenuGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    items: list[ShowcaseNavigationMenuItem] = Field(default_factory=list)


class ShowcaseNavigationMenuBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str | None = None
    titleTarget: ShowcaseRouteTarget | None = None
    items: list[ShowcaseNavigationMenuItem] = Field(default_factory=list)
    groups: list[ShowcaseNavigationMenuGroup] = Field(default_factory=list)


class ShowcaseNavigationMenuFooterLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    target: ShowcaseRouteTarget


class ShowcaseNavigationMenu(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    layout: Literal["new", "designers", "category_columns"]
    blocks: list[ShowcaseNavigationMenuBlock] = Field(default_factory=list)
    footerLink: ShowcaseNavigationMenuFooterLink | None = None


class ShowcaseNavigationSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Literal["new", "designers", "men", "women", "sale"]
    label: str
    target: ShowcaseRouteTarget | None = None
    menu: ShowcaseNavigationMenu | None = None


class ShowcaseNavigationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: list[ShowcaseNavigationSection] = Field(default_factory=list)


class CatalogFilterOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    value: str


class CatalogFilterActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    target: ShowcaseRouteTarget
    carryKeys: list[str] = Field(default_factory=list)
    emphasis: Literal["default", "strong"] | None = None


class CatalogFilterGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    queryParam: str
    selectionMode: Literal["single", "multiple"]
    options: list[CatalogFilterOption] = Field(default_factory=list)
    visibleOptionsLimit: int | None = None
    actionItem: CatalogFilterActionItem | None = None
    emptyState: str | None = None
    panelWidth: Literal["compact", "wide"] | None = None
    maxVisibleOptions: int | None = None
    prioritizeSelected: bool | None = None


class CatalogPageHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str | None = None
    source: Literal["search", "sale", "custom_catalog", "designer", "menu_filter", "all_products", "multiple_designers", "catalog"]


class CatalogViewContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Literal["default", "designers", "sale"]
    header: CatalogPageHeader
    globalConstraints: list[str] | None = None


class CatalogPreviewMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    value: str


class CatalogExperienceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: CatalogViewContext
    filterGroups: list[CatalogFilterGroup] = Field(default_factory=list)
    previewMetrics: list[CatalogPreviewMetric] = Field(default_factory=list)


class ShowcaseDesignersDirectoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    letter: str


class ShowcaseDesignersDirectoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alphabet: list[str] = Field(default_factory=list)
    entries: list[ShowcaseDesignersDirectoryEntry] = Field(default_factory=list)
