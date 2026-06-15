"""cleanup parser_product legacy identity fields

Revision ID: 0040_parser_product_contract_cleanup
Revises: 0039_drop_parser_product_price
Create Date: 2026-06-14 03:20:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = "0040_parser_product_contract_cleanup"
down_revision = "0039_drop_parser_product_price"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = :table
              AND column_name = :column
            LIMIT 1
            """
        ),
        {"table": table, "column": column},
    ).first()
    return row is not None


def _index_exists(index_name: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(
        text(
            """
            SELECT 1
            FROM pg_indexes
            WHERE schemaname = ANY(current_schemas(false))
              AND indexname = :index_name
            LIMIT 1
            """
        ),
        {"index_name": index_name},
    ).first()
    return row is not None


def upgrade() -> None:
    if _index_exists("idx_parser_product_source_canonical_url"):
        op.drop_index("idx_parser_product_source_canonical_url", table_name="parser_product")

    if _column_exists("parser_product", "canonical_url"):
        with op.batch_alter_table("parser_product") as batch_op:
            batch_op.drop_column("canonical_url")

    if _index_exists("idx_parser_product_source_external_id"):
        op.drop_index("idx_parser_product_source_external_id", table_name="parser_product")

    if _column_exists("parser_product", "source_external_id") and not _column_exists("parser_product", "external_id"):
        with op.batch_alter_table("parser_product") as batch_op:
            batch_op.alter_column("source_external_id", new_column_name="external_id")

    if _column_exists("parser_product", "external_id") and not _index_exists("idx_parser_product_external_id"):
        op.create_index("idx_parser_product_external_id", "parser_product", ["source_id", "external_id"], unique=False)


def downgrade() -> None:
    if _index_exists("idx_parser_product_external_id"):
        op.drop_index("idx_parser_product_external_id", table_name="parser_product")

    if _column_exists("parser_product", "external_id") and not _column_exists("parser_product", "source_external_id"):
        with op.batch_alter_table("parser_product") as batch_op:
            batch_op.alter_column("external_id", new_column_name="source_external_id")

    if _column_exists("parser_product", "source_external_id") and not _index_exists("idx_parser_product_source_external_id"):
        op.create_index(
            "idx_parser_product_source_external_id",
            "parser_product",
            ["source_id", "source_external_id"],
            unique=False,
        )

    if not _column_exists("parser_product", "canonical_url"):
        with op.batch_alter_table("parser_product") as batch_op:
            batch_op.add_column(sa.Column("canonical_url", sa.String(length=2048), nullable=True))

    if not _index_exists("idx_parser_product_source_canonical_url"):
        op.create_index(
            "idx_parser_product_source_canonical_url",
            "parser_product",
            ["source_id", "canonical_url"],
            unique=False,
        )
