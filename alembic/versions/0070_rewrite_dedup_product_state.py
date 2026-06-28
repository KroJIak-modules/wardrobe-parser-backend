"""rewrite dedup product state

Revision ID: 0070_rewrite_dedup_product_state
Revises: 0069_drop_source_host_normalized
Create Date: 2026-06-24 18:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0070_rewrite_dedup_product_state"
down_revision = "0069_drop_source_host_normalized"
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _has_index(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _has_check(bind, table_name: str, check_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(check.get("name") == check_name for check in inspector.get_check_constraints(table_name))


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "products", "dedup_status"):
        op.add_column(
            "products",
            sa.Column("dedup_status", sa.String(length=32), nullable=False, server_default="independent"),
        )
    if not _has_column(bind, "products", "dedup_decision_id"):
        op.add_column("products", sa.Column("dedup_decision_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            "fk_products_dedup_decision_id",
            "products",
            "product_dedup_decisions",
            ["dedup_decision_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if not _has_column(bind, "products", "dedup_target_product_id"):
        op.add_column("products", sa.Column("dedup_target_product_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            "fk_products_dedup_target_product_id",
            "products",
            "products",
            ["dedup_target_product_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if not _has_index(bind, "products", "ix_products_dedup_decision_id"):
        op.create_index("ix_products_dedup_decision_id", "products", ["dedup_decision_id"], unique=False)
    if not _has_index(bind, "products", "ix_products_dedup_target_product_id"):
        op.create_index("ix_products_dedup_target_product_id", "products", ["dedup_target_product_id"], unique=False)

    if not _has_column(bind, "product_listing_members", "membership_kind"):
        op.add_column(
            "product_listing_members",
            sa.Column("membership_kind", sa.String(length=16), nullable=False, server_default="owner"),
        )

    if _has_check(bind, "product_dedup_decisions", "ck_product_dedup_decisions_created_product_merge_only"):
        op.drop_constraint(
            "ck_product_dedup_decisions_created_product_merge_only",
            "product_dedup_decisions",
            type_="check",
        )
    if _has_check(bind, "product_dedup_decisions", "ck_product_dedup_decisions_decision_kind"):
        op.drop_constraint("ck_product_dedup_decisions_decision_kind", "product_dedup_decisions", type_="check")

    op.execute(
        sa.text(
            """
            UPDATE product_dedup_decisions
            SET decision_kind = CASE
                WHEN decision_kind = 'merge' AND COALESCE(NULLIF(undo_payload ->> 'decision_action', ''), '') = 'keep_left' THEN 'keep_left'
                WHEN decision_kind = 'merge' AND COALESCE(NULLIF(undo_payload ->> 'decision_action', ''), '') = 'keep_right' THEN 'keep_right'
                WHEN decision_kind = 'merge' THEN 'combine'
                ELSE decision_kind
            END
            """
        )
    )

    op.execute(sa.text("UPDATE products SET dedup_status = 'combined_source' WHERE lifecycle_status = 'merged'"))

    op.execute(
        sa.text(
            """
            WITH latest_decisions AS (
                SELECT DISTINCT ON (member.product_id)
                    member.product_id,
                    decision.id AS decision_id,
                    decision.created_product_id
                FROM product_dedup_decision_members AS member
                JOIN product_dedup_decisions AS decision ON decision.id = member.decision_id
                WHERE decision.decision_kind = 'combine'
                ORDER BY member.product_id, decision.id DESC
            )
            UPDATE products AS product
            SET
                dedup_decision_id = latest_decisions.decision_id,
                dedup_target_product_id = latest_decisions.created_product_id
            FROM latest_decisions
            WHERE product.id = latest_decisions.product_id
              AND product.lifecycle_status = 'merged'
            """
        )
    )
    op.execute(sa.text("UPDATE products SET lifecycle_status = 'active' WHERE lifecycle_status = 'merged'"))

    if _has_check(bind, "products", "ck_products_lifecycle_status"):
        op.drop_constraint("ck_products_lifecycle_status", "products", type_="check")
    op.create_check_constraint("ck_products_lifecycle_status", "products", "lifecycle_status IN ('active')")

    if _has_check(bind, "products", "ck_products_dedup_status"):
        op.drop_constraint("ck_products_dedup_status", "products", type_="check")
    op.create_check_constraint(
        "ck_products_dedup_status",
        "products",
        "dedup_status IN ('independent', 'combined_source', 'hidden_by_keep')",
    )

    op.create_check_constraint(
        "ck_product_dedup_decisions_decision_kind",
        "product_dedup_decisions",
        "decision_kind IN ('reject', 'combine', 'keep_left', 'keep_right')",
    )

    op.create_check_constraint(
        "ck_product_dedup_decisions_created_product_merge_only",
        "product_dedup_decisions",
        """
        (decision_kind = 'combine' AND created_product_id IS NOT NULL)
        OR
        (decision_kind IN ('reject', 'keep_left', 'keep_right') AND created_product_id IS NULL)
        """,
    )

    if _has_check(bind, "product_listing_members", "ck_product_listing_members_membership_kind"):
        op.drop_constraint("ck_product_listing_members_membership_kind", "product_listing_members", type_="check")
    op.create_check_constraint(
        "ck_product_listing_members_membership_kind",
        "product_listing_members",
        "membership_kind IN ('owner', 'included')",
    )

    op.execute(sa.text("ALTER TABLE product_listing_members DROP CONSTRAINT IF EXISTS uq_product_listing_members_listing_id"))
    if not _has_index(bind, "product_listing_members", "ux_product_listing_members_owner_listing_id"):
        op.create_index(
            "ux_product_listing_members_owner_listing_id",
            "product_listing_members",
            ["listing_id"],
            unique=True,
            postgresql_where=sa.text("membership_kind = 'owner'"),
        )


def downgrade() -> None:
    bind = op.get_bind()

    op.execute(sa.text("ALTER TABLE product_listing_members DROP CONSTRAINT IF EXISTS ck_product_listing_members_membership_kind"))
    op.execute(sa.text("DROP INDEX IF EXISTS ux_product_listing_members_owner_listing_id"))
    op.create_unique_constraint("uq_product_listing_members_listing_id", "product_listing_members", ["listing_id"])
    if _has_column(bind, "product_listing_members", "membership_kind"):
        op.drop_column("product_listing_members", "membership_kind")

    if _has_check(bind, "product_dedup_decisions", "ck_product_dedup_decisions_created_product_merge_only"):
        op.drop_constraint(
            "ck_product_dedup_decisions_created_product_merge_only",
            "product_dedup_decisions",
            type_="check",
        )
    op.create_check_constraint(
        "ck_product_dedup_decisions_created_product_merge_only",
        "product_dedup_decisions",
        "(decision_kind = 'merge' AND created_product_id IS NOT NULL) OR (decision_kind <> 'merge' AND created_product_id IS NULL)",
    )

    if _has_check(bind, "product_dedup_decisions", "ck_product_dedup_decisions_decision_kind"):
        op.drop_constraint("ck_product_dedup_decisions_decision_kind", "product_dedup_decisions", type_="check")
    op.create_check_constraint(
        "ck_product_dedup_decisions_decision_kind",
        "product_dedup_decisions",
        "decision_kind IN ('reject', 'merge')",
    )

    if _has_check(bind, "products", "ck_products_dedup_status"):
        op.drop_constraint("ck_products_dedup_status", "products", type_="check")
    if _has_check(bind, "products", "ck_products_lifecycle_status"):
        op.drop_constraint("ck_products_lifecycle_status", "products", type_="check")
    op.create_check_constraint("ck_products_lifecycle_status", "products", "lifecycle_status IN ('active', 'merged')")

    if _has_index(bind, "products", "ix_products_dedup_target_product_id"):
        op.drop_index("ix_products_dedup_target_product_id", table_name="products")
    if _has_index(bind, "products", "ix_products_dedup_decision_id"):
        op.drop_index("ix_products_dedup_decision_id", table_name="products")
    op.execute(sa.text("ALTER TABLE products DROP CONSTRAINT IF EXISTS fk_products_dedup_target_product_id"))
    op.execute(sa.text("ALTER TABLE products DROP CONSTRAINT IF EXISTS fk_products_dedup_decision_id"))
    if _has_column(bind, "products", "dedup_target_product_id"):
        op.drop_column("products", "dedup_target_product_id")
    if _has_column(bind, "products", "dedup_decision_id"):
        op.drop_column("products", "dedup_decision_id")
    if _has_column(bind, "products", "dedup_status"):
        op.drop_column("products", "dedup_status")
