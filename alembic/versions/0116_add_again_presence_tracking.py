"""track automatic catalog reappearance for AGAIN badges

Revision ID: 0116_add_again_presence_tracking
Revises: 0115_drop_admin_payment_comment
"""

from alembic import op
import sqlalchemy as sa


revision = "0116_add_again_presence_tracking"
down_revision = "0115_drop_admin_payment_comment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_presence_state",
        sa.Column("entity_kind", sa.String(length=16), nullable=False),
        sa.Column("entity_key", sa.String(length=255), nullable=False),
        sa.Column("product_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("is_auto_tracked", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_reappeared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "entity_kind IN ('product', 'brand', 'designer')",
            name="ck_catalog_presence_state_entity_kind",
        ),
        sa.CheckConstraint(
            "product_count >= 0",
            name="ck_catalog_presence_state_product_count_non_negative",
        ),
        sa.PrimaryKeyConstraint("entity_kind", "entity_key"),
    )
    op.create_index(
        "ix_catalog_presence_state_reappearance",
        "catalog_presence_state",
        ["entity_kind", "last_reappeared_at"],
    )
    op.create_table(
        "admin_again_view",
        sa.Column("admin_user_id", sa.Integer(), nullable=False),
        sa.Column("entity_kind", sa.String(length=16), nullable=False),
        sa.Column("entity_key", sa.String(length=255), nullable=False),
        sa.Column("viewed_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entity_kind IN ('product', 'brand', 'designer')",
            name="ck_admin_again_view_entity_kind",
        ),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("admin_user_id", "entity_kind", "entity_key"),
    )
    op.create_index(
        "ix_admin_again_view_entity",
        "admin_again_view",
        ["entity_kind", "entity_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_admin_again_view_entity", table_name="admin_again_view")
    op.drop_table("admin_again_view")
    op.drop_index("ix_catalog_presence_state_reappearance", table_name="catalog_presence_state")
    op.drop_table("catalog_presence_state")
