"""catalog v2 source and variant constraints

Revision ID: 0049_catalog_v2_source_and_variant_constraints
Revises: 0048_backfill_source_sync_state_status
Create Date: 2026-06-17 03:10:00.000000
"""

from __future__ import annotations

from urllib.parse import urlparse

from alembic import op
import sqlalchemy as sa


revision = "0049_catalog_v2_source_and_variant_constraints"
down_revision = "0048_backfill_source_sync_state_status"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _constraint_exists(bind, table_name: str, constraint_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(constraint.get("name") == constraint_name for constraint in inspector.get_unique_constraints(table_name))


def _normalize_host(raw: str | None) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = str(parsed.hostname or parsed.netloc or parsed.path or "").strip().lower()
    except Exception:
        host = value.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def upgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "sources") and not _has_column(bind, "sources", "base_url_normalized"):
        op.add_column("sources", sa.Column("base_url_normalized", sa.String(length=255), nullable=True))
    if _table_exists(bind, "sources") and _has_column(bind, "sources", "base_url_normalized"):
        rows = bind.execute(sa.text("SELECT id, base_url FROM sources")).mappings().all()
        for row in rows:
            bind.execute(
                sa.text("UPDATE sources SET base_url_normalized = :value WHERE id = :id"),
                {"id": int(row["id"]), "value": _normalize_host(row["base_url"])},
            )
        op.alter_column("sources", "base_url_normalized", existing_type=sa.String(length=255), nullable=False)
        if not _constraint_exists(bind, "sources", "uq_sources_base_url_normalized"):
            op.create_unique_constraint("uq_sources_base_url_normalized", "sources", ["base_url_normalized"])

    if _table_exists(bind, "product_listing_variants") and not _constraint_exists(
        bind,
        "product_listing_variants",
        "uq_product_listing_variants_listing_source_ref_id",
    ):
        op.create_unique_constraint(
            "uq_product_listing_variants_listing_source_ref_id",
            "product_listing_variants",
            ["listing_id", "source_ref_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "product_listing_variants") and _constraint_exists(
        bind,
        "product_listing_variants",
        "uq_product_listing_variants_listing_source_ref_id",
    ):
        op.drop_constraint(
            "uq_product_listing_variants_listing_source_ref_id",
            "product_listing_variants",
            type_="unique",
        )

    if _table_exists(bind, "sources") and _has_column(bind, "sources", "base_url_normalized"):
        if _constraint_exists(bind, "sources", "uq_sources_base_url_normalized"):
            op.drop_constraint("uq_sources_base_url_normalized", "sources", type_="unique")
        op.drop_column("sources", "base_url_normalized")
