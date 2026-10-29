"""add admin payments

Revision ID: 0114_admin_payments
Revises: 0113_add_shipping_alt_threshold_eur
"""
from alembic import op
import sqlalchemy as sa

revision = "0114_admin_payments"
down_revision = "0113_add_shipping_alt_threshold_eur"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.schema.CreateSequence(sa.Sequence("admin_payment_order_seq")))
    op.create_table(
        "admin_payment",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("order_number", sa.String(100), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(40)),
        sa.Column("customer_name", sa.String(255)),
        sa.Column("address", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payment_url", sa.Text()),
        sa.Column("provider_payment_id", sa.String(255)),
        sa.Column("ownership_token", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_admin_payment_created_id", "admin_payment", ["created_at", "id"])


def downgrade():
    op.drop_table("admin_payment")
    op.execute(sa.schema.DropSequence(sa.Sequence("admin_payment_order_seq")))
