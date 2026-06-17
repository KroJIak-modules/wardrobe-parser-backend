"""catalog v2 hardening

Revision ID: 0047_catalog_v2_hardening
Revises: 0046_rename_admin_ui_showcase_columns
Create Date: 2026-06-17 00:30:00.000000
"""

from __future__ import annotations

from urllib.parse import urlparse

from alembic import op
import sqlalchemy as sa


revision = "0047_catalog_v2_hardening"
down_revision = "0046_rename_admin_ui_showcase_columns"
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


def _check_exists(bind, table_name: str, check_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(check.get("name") == check_name for check in inspector.get_check_constraints(table_name))


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


def _normalize_listing_url(raw: str | None) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = _normalize_host(value)
    path = (parsed.path or "/").strip() or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return f"{host}{path}" if host else path


def upgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "sources") and not _has_column(bind, "sources", "host_normalized"):
        op.add_column("sources", sa.Column("host_normalized", sa.String(length=255), nullable=True))
    if _table_exists(bind, "product_listings") and not _has_column(bind, "product_listings", "url_normalized"):
        op.add_column("product_listings", sa.Column("url_normalized", sa.String(length=2048), nullable=True))
    if _table_exists(bind, "product_listings") and not _has_column(bind, "product_listings", "host_normalized"):
        op.add_column("product_listings", sa.Column("host_normalized", sa.String(length=255), nullable=True))

    if _table_exists(bind, "sources") and _has_column(bind, "sources", "host_normalized"):
        rows = bind.execute(sa.text("SELECT id, base_url FROM sources")).mappings().all()
        for row in rows:
            bind.execute(
                sa.text("UPDATE sources SET host_normalized = :host WHERE id = :id"),
                {"id": int(row["id"]), "host": _normalize_host(row["base_url"])},
            )
        op.alter_column("sources", "host_normalized", existing_type=sa.String(length=255), nullable=False)
        if not _index_exists(bind, "sources", "ix_sources_host_normalized"):
            op.create_index("ix_sources_host_normalized", "sources", ["host_normalized"], unique=False)

    if _table_exists(bind, "product_listings") and _has_column(bind, "product_listings", "url_normalized") and _has_column(bind, "product_listings", "host_normalized"):
        rows = bind.execute(sa.text("SELECT id, url FROM product_listings")).mappings().all()
        for row in rows:
            bind.execute(
                sa.text(
                    "UPDATE product_listings SET url_normalized = :url_normalized, host_normalized = :host_normalized WHERE id = :id"
                ),
                {
                    "id": int(row["id"]),
                    "url_normalized": _normalize_listing_url(row["url"]),
                    "host_normalized": _normalize_host(row["url"]),
                },
            )
        op.alter_column("product_listings", "url_normalized", existing_type=sa.String(length=2048), nullable=False)
        op.alter_column("product_listings", "host_normalized", existing_type=sa.String(length=255), nullable=False)
        if not _index_exists(bind, "product_listings", "idx_product_listings_url_normalized"):
            op.create_index("idx_product_listings_url_normalized", "product_listings", ["url_normalized"], unique=False)
        if not _index_exists(bind, "product_listings", "idx_product_listings_host_handle"):
            op.create_index("idx_product_listings_host_handle", "product_listings", ["host_normalized", "handle"], unique=False)

    if _table_exists(bind, "product_price_overrides"):
        if not _check_exists(bind, "product_price_overrides", "ck_product_price_overrides_manual_price_positive"):
            op.create_check_constraint(
                "ck_product_price_overrides_manual_price_positive",
                "product_price_overrides",
                "manual_price_rub > 0",
            )
        if not _check_exists(bind, "product_price_overrides", "ck_product_price_overrides_compare_at_gt_price"):
            op.create_check_constraint(
                "ck_product_price_overrides_compare_at_gt_price",
                "product_price_overrides",
                "manual_compare_at_price_rub IS NULL OR manual_compare_at_price_rub > manual_price_rub",
            )

    if _table_exists(bind, "product_listing_gallery_images") and not _check_exists(bind, "product_listing_gallery_images", "ck_product_listing_gallery_images_origin_target"):
        op.create_check_constraint(
            "ck_product_listing_gallery_images_origin_target",
            "product_listing_gallery_images",
            """
            (origin_kind = 'source_image' AND listing_image_id IS NOT NULL AND image_asset_id IS NULL)
            OR
            (origin_kind = 'uploaded_asset' AND listing_image_id IS NULL AND image_asset_id IS NOT NULL)
            """,
        )

    if _table_exists(bind, "product_dedup_decisions") and not _check_exists(bind, "product_dedup_decisions", "ck_product_dedup_decisions_created_product_merge_only"):
        op.create_check_constraint(
            "ck_product_dedup_decisions_created_product_merge_only",
            "product_dedup_decisions",
            "(decision_kind = 'merge' AND created_product_id IS NOT NULL) OR (decision_kind <> 'merge' AND created_product_id IS NULL)",
        )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_products_primary_listing_membership()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.primary_listing_id IS NULL THEN
                RETURN NEW;
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM product_listing_members
                WHERE product_id = NEW.id
                  AND listing_id = NEW.primary_listing_id
            ) THEN
                RAISE EXCEPTION 'primary_listing_id % does not belong to product %', NEW.primary_listing_id, NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_validate_products_primary_listing_membership ON products;")
    op.execute(
        """
        CREATE TRIGGER trg_validate_products_primary_listing_membership
        BEFORE INSERT OR UPDATE OF primary_listing_id ON products
        FOR EACH ROW
        EXECUTE FUNCTION validate_products_primary_listing_membership();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_product_listing_gallery_scope()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.origin_kind = 'source_image' THEN
                IF NEW.listing_image_id IS NULL OR NEW.image_asset_id IS NOT NULL THEN
                    RAISE EXCEPTION 'source_image row must have listing_image_id only';
                END IF;
            ELSIF NEW.origin_kind = 'uploaded_asset' THEN
                IF NEW.listing_image_id IS NOT NULL OR NEW.image_asset_id IS NULL THEN
                    RAISE EXCEPTION 'uploaded_asset row must have image_asset_id only';
                END IF;
            ELSE
                RAISE EXCEPTION 'unsupported origin_kind: %', NEW.origin_kind;
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM product_listing_members
                WHERE product_id = NEW.product_id
                  AND listing_id = NEW.listing_id
            ) THEN
                RAISE EXCEPTION 'gallery scope listing % does not belong to product %', NEW.listing_id, NEW.product_id;
            END IF;

            IF NEW.listing_image_id IS NOT NULL AND NOT EXISTS (
                SELECT 1
                FROM product_listing_images
                WHERE id = NEW.listing_image_id
                  AND listing_id = NEW.listing_id
            ) THEN
                RAISE EXCEPTION 'listing_image_id % does not belong to listing %', NEW.listing_image_id, NEW.listing_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_validate_product_listing_gallery_scope ON product_listing_gallery_images;")
    op.execute(
        """
        CREATE TRIGGER trg_validate_product_listing_gallery_scope
        BEFORE INSERT OR UPDATE ON product_listing_gallery_images
        FOR EACH ROW
        EXECUTE FUNCTION validate_product_listing_gallery_scope();
        """
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.execute("DROP TRIGGER IF EXISTS trg_validate_product_listing_gallery_scope ON product_listing_gallery_images;")
    op.execute("DROP FUNCTION IF EXISTS validate_product_listing_gallery_scope();")
    op.execute("DROP TRIGGER IF EXISTS trg_validate_products_primary_listing_membership ON products;")
    op.execute("DROP FUNCTION IF EXISTS validate_products_primary_listing_membership();")

    if _table_exists(bind, "product_dedup_decisions") and _check_exists(bind, "product_dedup_decisions", "ck_product_dedup_decisions_created_product_merge_only"):
        op.drop_constraint("ck_product_dedup_decisions_created_product_merge_only", "product_dedup_decisions", type_="check")
    if _table_exists(bind, "product_listing_gallery_images") and _check_exists(bind, "product_listing_gallery_images", "ck_product_listing_gallery_images_origin_target"):
        op.drop_constraint("ck_product_listing_gallery_images_origin_target", "product_listing_gallery_images", type_="check")
    if _table_exists(bind, "product_price_overrides") and _check_exists(bind, "product_price_overrides", "ck_product_price_overrides_compare_at_gt_price"):
        op.drop_constraint("ck_product_price_overrides_compare_at_gt_price", "product_price_overrides", type_="check")
    if _table_exists(bind, "product_price_overrides") and _check_exists(bind, "product_price_overrides", "ck_product_price_overrides_manual_price_positive"):
        op.drop_constraint("ck_product_price_overrides_manual_price_positive", "product_price_overrides", type_="check")

    if _table_exists(bind, "product_listings") and _has_column(bind, "product_listings", "host_normalized"):
        if _index_exists(bind, "product_listings", "idx_product_listings_host_handle"):
            op.drop_index("idx_product_listings_host_handle", table_name="product_listings")
        op.drop_column("product_listings", "host_normalized")
    if _table_exists(bind, "product_listings") and _has_column(bind, "product_listings", "url_normalized"):
        if _index_exists(bind, "product_listings", "idx_product_listings_url_normalized"):
            op.drop_index("idx_product_listings_url_normalized", table_name="product_listings")
        op.drop_column("product_listings", "url_normalized")
    if _table_exists(bind, "sources") and _has_column(bind, "sources", "host_normalized"):
        if _index_exists(bind, "sources", "ix_sources_host_normalized"):
            op.drop_index("ix_sources_host_normalized", table_name="sources")
        op.drop_column("sources", "host_normalized")
