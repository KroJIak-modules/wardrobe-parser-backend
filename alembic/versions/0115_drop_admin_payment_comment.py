"""remove unused admin payment comment

Revision ID: 0115_drop_admin_payment_comment
Revises: 0114_admin_payments
"""
from alembic import op

revision = "0115_drop_admin_payment_comment"
down_revision = "0114_admin_payments"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE admin_payment DROP COLUMN IF EXISTS comment")


def downgrade():
    raise NotImplementedError("Irreversible migration")
