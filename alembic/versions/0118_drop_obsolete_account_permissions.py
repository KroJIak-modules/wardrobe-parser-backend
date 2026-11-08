"""remove obsolete account permissions from existing role records

Revision ID: 0118_drop_obsolete_account_permissions
Revises: 0117_expand_admin_permission_scopes
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0118_drop_obsolete_account_permissions"
down_revision = "0117_expand_admin_permission_scopes"
branch_labels = None
depends_on = None


_OBSOLETE_PERMISSION_KEYS = {"accounts.read", "accounts.edit"}


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, permissions FROM admin_role")).mappings()
    for row in rows:
        permissions = row["permissions"] if isinstance(row["permissions"], list) else []
        retained = [str(permission) for permission in permissions if str(permission) not in _OBSOLETE_PERMISSION_KEYS]
        if retained != permissions:
            bind.execute(
                sa.text("UPDATE admin_role SET permissions = CAST(:permissions AS jsonb) WHERE id = :role_id"),
                {"permissions": json.dumps(retained), "role_id": int(row["id"])},
            )


def downgrade() -> None:
    # Account administration is intentionally superadmin-only, so no role-level
    # permissions are restored.
    return None
