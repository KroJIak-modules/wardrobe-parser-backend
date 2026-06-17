"""create catalog v2 taxonomy tables

Revision ID: 0045_create_catalog_v2_taxonomy
Revises: 0044_create_catalog_v2_core
Create Date: 2026-06-17 00:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0045_create_catalog_v2_taxonomy"
down_revision = "0044_create_catalog_v2_core"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _create_index_if_missing(bind, name: str, table_name: str, columns: list[str], *, unique: bool = False) -> None:
    if _index_exists(bind, table_name, name):
        return
    op.create_index(name, table_name, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "filters"):
        op.create_table(
            "filters",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("display_title", sa.Text(), nullable=True),
            sa.Column("slug", sa.String(length=255), nullable=False, unique=True),
            sa.Column("node_kind", sa.String(length=16), nullable=False, server_default=sa.text("'filter'")),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    if not _table_exists(bind, "filter_nodes"):
        op.create_table(
            "filter_nodes",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("filter_id", sa.BigInteger(), sa.ForeignKey("filters.id", ondelete="CASCADE"), nullable=False),
            sa.Column("parent_node_id", sa.BigInteger(), sa.ForeignKey("filter_nodes.id", ondelete="CASCADE"), nullable=True),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.UniqueConstraint("filter_id", name="uq_filter_nodes_filter_id"),
            sa.UniqueConstraint("parent_node_id", "position", name="uq_filter_nodes_parent_position"),
        )
        _create_index_if_missing(bind, "ix_filter_nodes_filter_id", "filter_nodes", ["filter_id"])
        _create_index_if_missing(bind, "ix_filter_nodes_parent_node_id", "filter_nodes", ["parent_node_id"])

    if not _table_exists(bind, "filter_local_category_keywords"):
        op.create_table(
            "filter_local_category_keywords",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("filter_id", sa.BigInteger(), sa.ForeignKey("filters.id", ondelete="CASCADE"), nullable=False),
            sa.Column("keyword", sa.Text(), nullable=False),
            sa.UniqueConstraint("filter_id", "keyword", name="uq_filter_local_category_keywords_filter_keyword"),
        )
        _create_index_if_missing(bind, "ix_filter_local_category_keywords_filter_id", "filter_local_category_keywords", ["filter_id"])

    if not _table_exists(bind, "filter_title_keywords"):
        op.create_table(
            "filter_title_keywords",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("filter_id", sa.BigInteger(), sa.ForeignKey("filters.id", ondelete="CASCADE"), nullable=False),
            sa.Column("keyword", sa.Text(), nullable=False),
            sa.UniqueConstraint("filter_id", "keyword", name="uq_filter_title_keywords_filter_keyword"),
        )
        _create_index_if_missing(bind, "ix_filter_title_keywords_filter_id", "filter_title_keywords", ["filter_id"])

    if not _table_exists(bind, "filter_manual_products"):
        op.create_table(
            "filter_manual_products",
            sa.Column("filter_id", sa.BigInteger(), sa.ForeignKey("filters.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
        )

    if not _table_exists(bind, "custom_catalogs"):
        op.create_table(
            "custom_catalogs",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("slug", sa.String(length=255), nullable=False, unique=True),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    if not _table_exists(bind, "custom_catalog_products"):
        op.create_table(
            "custom_catalog_products",
            sa.Column("catalog_id", sa.BigInteger(), sa.ForeignKey("custom_catalogs.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
        )

    if not _table_exists(bind, "showcase_categories"):
        op.create_table(
            "showcase_categories",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("code", sa.String(length=32), nullable=False, unique=True),
            sa.Column("title", sa.Text(), nullable=False),
        )
        op.bulk_insert(
            sa.table(
                "showcase_categories",
                sa.column("id", sa.BigInteger()),
                sa.column("code", sa.String()),
                sa.column("title", sa.Text()),
            ),
            [
                {"id": 1, "code": "new", "title": "Новинки"},
                {"id": 2, "code": "designers", "title": "Дизайнеры"},
                {"id": 3, "code": "men", "title": "Мужское"},
                {"id": 4, "code": "women", "title": "Женское"},
                {"id": 5, "code": "sale", "title": "Sale"},
            ],
        )

    if not _table_exists(bind, "showcase_category_attachments"):
        op.create_table(
            "showcase_category_attachments",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("showcase_category_id", sa.BigInteger(), sa.ForeignKey("showcase_categories.id", ondelete="CASCADE"), nullable=False),
            sa.Column("attachment_kind", sa.String(length=32), nullable=False),
            sa.Column("filter_id", sa.BigInteger(), sa.ForeignKey("filters.id", ondelete="CASCADE"), nullable=True),
            sa.Column("custom_catalog_id", sa.BigInteger(), sa.ForeignKey("custom_catalogs.id", ondelete="CASCADE"), nullable=True),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.UniqueConstraint("showcase_category_id", "position", name="uq_showcase_category_attachments_category_position"),
            sa.CheckConstraint(
                """
                (attachment_kind = 'filter' AND filter_id IS NOT NULL AND custom_catalog_id IS NULL)
                OR
                (attachment_kind = 'custom_catalog' AND filter_id IS NULL AND custom_catalog_id IS NOT NULL)
                """,
                name="ck_showcase_category_attachments_target",
            ),
        )
        _create_index_if_missing(bind, "ix_showcase_category_attachments_showcase_category_id", "showcase_category_attachments", ["showcase_category_id"])
        _create_index_if_missing(bind, "ix_showcase_category_attachments_filter_id", "showcase_category_attachments", ["filter_id"])
        _create_index_if_missing(bind, "ix_showcase_category_attachments_custom_catalog_id", "showcase_category_attachments", ["custom_catalog_id"])

    if not _table_exists(bind, "showcase_category_attachment_hidden_nodes"):
        op.create_table(
            "showcase_category_attachment_hidden_nodes",
            sa.Column("attachment_id", sa.BigInteger(), sa.ForeignKey("showcase_category_attachments.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("filter_node_id", sa.BigInteger(), sa.ForeignKey("filter_nodes.id", ondelete="CASCADE"), primary_key=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in (
        "showcase_category_attachment_hidden_nodes",
        "showcase_category_attachments",
        "showcase_categories",
        "custom_catalog_products",
        "custom_catalogs",
        "filter_manual_products",
        "filter_title_keywords",
        "filter_local_category_keywords",
        "filter_nodes",
        "filters",
    ):
        if _table_exists(bind, table_name):
            op.drop_table(table_name)
