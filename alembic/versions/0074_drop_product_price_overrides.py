"""drop product price overrides

Revision ID: 0074_drop_product_price_overrides
Revises: 0073_add_product_brand_override
Create Date: 2026-06-26 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


# revision identifiers, used by Alembic.
revision = "0074_drop_product_price_overrides"
down_revision = "0073_add_product_brand_override"
branch_labels = None
depends_on = None


def _table_exists(bind: Connection, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "product_price_overrides"):
        op.drop_table("product_price_overrides")


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "product_price_overrides"):
        return
    op.create_table(
        "product_price_overrides",
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("manual_price_rub", sa.Numeric(12, 2), nullable=False),
        sa.Column("manual_compare_at_price_rub", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("manual_price_rub > 0", name="ck_product_price_overrides_manual_price_positive"),
        sa.CheckConstraint(
            "manual_compare_at_price_rub IS NULL OR manual_compare_at_price_rub > manual_price_rub",
            name="ck_product_price_overrides_compare_at_gt_price",
        ),
    )
