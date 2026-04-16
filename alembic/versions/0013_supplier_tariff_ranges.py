"""replace supplier step-rates with range-rates

Revision ID: 0013_supplier_tariff_ranges
Revises: 0012_designers_excl_store
Create Date: 2026-04-16 18:40:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013_supplier_tariff_ranges"
down_revision = "0012_designers_excl_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parser_supplier_shipping_rate",
        sa.Column("min_kg", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "parser_supplier_shipping_rate",
        sa.Column("max_kg", sa.Float(), nullable=True),
    )

    op.execute(
        """
        UPDATE parser_supplier_shipping_rate
        SET min_kg = GREATEST(0.0, (step_500g - 1) * 0.5),
            max_kg = step_500g * 0.5
        """
    )

    op.drop_constraint(
        "uq_parser_supplier_shipping_rate_supplier_step",
        "parser_supplier_shipping_rate",
        type_="unique",
    )
    op.drop_index("idx_parser_supplier_shipping_rate_step_500g", table_name="parser_supplier_shipping_rate")
    op.drop_column("parser_supplier_shipping_rate", "step_500g")
    op.create_index("idx_parser_supplier_shipping_rate_min_kg", "parser_supplier_shipping_rate", ["min_kg"])
    op.create_index("idx_parser_supplier_shipping_rate_max_kg", "parser_supplier_shipping_rate", ["max_kg"])
    op.alter_column("parser_supplier_shipping_rate", "min_kg", server_default=None)


def downgrade() -> None:
    op.add_column(
        "parser_supplier_shipping_rate",
        sa.Column("step_500g", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute(
        """
        UPDATE parser_supplier_shipping_rate
        SET step_500g = GREATEST(
            1,
            CEIL(COALESCE(max_kg, min_kg + 0.5) / 0.5)::integer
        )
        """
    )
    op.drop_index("idx_parser_supplier_shipping_rate_max_kg", table_name="parser_supplier_shipping_rate")
    op.drop_index("idx_parser_supplier_shipping_rate_min_kg", table_name="parser_supplier_shipping_rate")
    op.create_index("idx_parser_supplier_shipping_rate_step_500g", "parser_supplier_shipping_rate", ["step_500g"])
    op.create_unique_constraint(
        "uq_parser_supplier_shipping_rate_supplier_step",
        "parser_supplier_shipping_rate",
        ["supplier_id", "step_500g"],
    )
    op.drop_column("parser_supplier_shipping_rate", "max_kg")
    op.drop_column("parser_supplier_shipping_rate", "min_kg")
