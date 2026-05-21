"""Add admin RBAC roles/users tables.

Revision ID: 0031_admin_rbac_users_roles
Revises: 0030_drop_parser_product_currency
Create Date: 2026-05-21 10:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0031_admin_rbac_users_roles"
down_revision = "0030_drop_parser_product_currency"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "admin_role"):
        op.create_table(
            "admin_role",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=128), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("idx_admin_role_name", "admin_role", ["name"], unique=False)

    if not _table_exists(bind, "admin_user"):
        op.create_table(
            "admin_user",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("login", sa.String(length=128), nullable=False, unique=True),
            sa.Column("password_hash", sa.String(length=512), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("role_id", sa.Integer(), sa.ForeignKey("admin_role.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("idx_admin_user_login", "admin_user", ["login"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "admin_user"):
        op.drop_index("idx_admin_user_login", table_name="admin_user")
        op.drop_table("admin_user")
    if _table_exists(bind, "admin_role"):
        op.drop_index("idx_admin_role_name", table_name="admin_role")
        op.drop_table("admin_role")
