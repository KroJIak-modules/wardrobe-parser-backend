from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class Filter(Base):
    __tablename__ = "filters"

    id = Column(BigInteger, primary_key=True)
    title = Column(Text, nullable=False)
    display_title = Column(Text, nullable=True)
    slug = Column(String(255), nullable=False, unique=True)
    node_kind = Column(String(16), nullable=False, default="filter", server_default="filter")
    is_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    node = relationship("FilterNode", back_populates="filter", uselist=False, cascade="all, delete-orphan")
    local_category_keywords = relationship("FilterLocalCategoryKeyword", back_populates="filter", cascade="all, delete-orphan")
    title_keywords = relationship("FilterTitleKeyword", back_populates="filter", cascade="all, delete-orphan")
    manual_products = relationship("FilterManualProduct", back_populates="filter", cascade="all, delete-orphan")
    showcase_attachments = relationship("ShowcaseCategoryAttachment", back_populates="filter")

    __table_args__ = (
        CheckConstraint("node_kind IN ('filter', 'multifilter')", name="ck_filters_node_kind"),
    )


class FilterNode(Base):
    __tablename__ = "filter_nodes"

    id = Column(BigInteger, primary_key=True)
    filter_id = Column(BigInteger, ForeignKey("filters.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    parent_node_id = Column(BigInteger, ForeignKey("filter_nodes.id", ondelete="CASCADE"), nullable=True, index=True)
    position = Column(Integer, nullable=False)

    filter = relationship("Filter", back_populates="node")
    parent_node = relationship("FilterNode", remote_side=[id], back_populates="children")
    children = relationship("FilterNode", back_populates="parent_node", cascade="all, delete-orphan")
    hidden_in_attachments = relationship("ShowcaseCategoryAttachmentHiddenNode", back_populates="filter_node", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("parent_node_id", "position", name="uq_filter_nodes_parent_position"),
    )


class FilterLocalCategoryKeyword(Base):
    __tablename__ = "filter_local_category_keywords"

    id = Column(BigInteger, primary_key=True)
    filter_id = Column(BigInteger, ForeignKey("filters.id", ondelete="CASCADE"), nullable=False, index=True)
    keyword = Column(Text, nullable=False)

    filter = relationship("Filter", back_populates="local_category_keywords")

    __table_args__ = (
        UniqueConstraint("filter_id", "keyword", name="uq_filter_local_category_keywords_filter_keyword"),
    )


class FilterTitleKeyword(Base):
    __tablename__ = "filter_title_keywords"

    id = Column(BigInteger, primary_key=True)
    filter_id = Column(BigInteger, ForeignKey("filters.id", ondelete="CASCADE"), nullable=False, index=True)
    keyword = Column(Text, nullable=False)

    filter = relationship("Filter", back_populates="title_keywords")

    __table_args__ = (
        UniqueConstraint("filter_id", "keyword", name="uq_filter_title_keywords_filter_keyword"),
    )


class FilterManualProduct(Base):
    __tablename__ = "filter_manual_products"

    filter_id = Column(BigInteger, ForeignKey("filters.id", ondelete="CASCADE"), primary_key=True)
    product_id = Column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)

    filter = relationship("Filter", back_populates="manual_products")
    product = relationship("Product")


class CustomCatalog(Base):
    __tablename__ = "custom_catalogs"

    id = Column(BigInteger, primary_key=True)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    slug = Column(String(255), nullable=False, unique=True)
    is_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    products = relationship("CustomCatalogProduct", back_populates="catalog", cascade="all, delete-orphan")
    showcase_attachments = relationship("ShowcaseCategoryAttachment", back_populates="custom_catalog")


class CustomCatalogProduct(Base):
    __tablename__ = "custom_catalog_products"

    catalog_id = Column(BigInteger, ForeignKey("custom_catalogs.id", ondelete="CASCADE"), primary_key=True)
    product_id = Column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)

    catalog = relationship("CustomCatalog", back_populates="products")
    product = relationship("Product")


class ShowcaseCategory(Base):
    __tablename__ = "showcase_categories"

    id = Column(BigInteger, primary_key=True)
    code = Column(String(32), nullable=False, unique=True)
    title = Column(Text, nullable=False)

    attachments = relationship("ShowcaseCategoryAttachment", back_populates="showcase_category", cascade="all, delete-orphan")


class ShowcaseCategoryAttachment(Base):
    __tablename__ = "showcase_category_attachments"

    id = Column(BigInteger, primary_key=True)
    showcase_category_id = Column(BigInteger, ForeignKey("showcase_categories.id", ondelete="CASCADE"), nullable=False, index=True)
    attachment_kind = Column(String(32), nullable=False)
    filter_id = Column(BigInteger, ForeignKey("filters.id", ondelete="CASCADE"), nullable=True, index=True)
    custom_catalog_id = Column(BigInteger, ForeignKey("custom_catalogs.id", ondelete="CASCADE"), nullable=True, index=True)
    position = Column(Integer, nullable=False)

    showcase_category = relationship("ShowcaseCategory", back_populates="attachments")
    filter = relationship("Filter", back_populates="showcase_attachments")
    custom_catalog = relationship("CustomCatalog", back_populates="showcase_attachments")
    hidden_nodes = relationship("ShowcaseCategoryAttachmentHiddenNode", back_populates="attachment", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("showcase_category_id", "position", name="uq_showcase_category_attachments_category_position"),
        CheckConstraint(
            """
            (attachment_kind = 'filter' AND filter_id IS NOT NULL AND custom_catalog_id IS NULL)
            OR
            (attachment_kind = 'custom_catalog' AND filter_id IS NULL AND custom_catalog_id IS NOT NULL)
            """,
            name="ck_showcase_category_attachments_target",
        ),
    )


class ShowcaseCategoryAttachmentHiddenNode(Base):
    __tablename__ = "showcase_category_attachment_hidden_nodes"

    attachment_id = Column(BigInteger, ForeignKey("showcase_category_attachments.id", ondelete="CASCADE"), primary_key=True)
    filter_node_id = Column(BigInteger, ForeignKey("filter_nodes.id", ondelete="CASCADE"), primary_key=True)

    attachment = relationship("ShowcaseCategoryAttachment", back_populates="hidden_nodes")
    filter_node = relationship("FilterNode", back_populates="hidden_in_attachments")
