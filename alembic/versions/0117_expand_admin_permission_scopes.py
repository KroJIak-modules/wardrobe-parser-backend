"""preserve existing role access while splitting admin permission scopes

Revision ID: 0117_expand_admin_permission_scopes
Revises: 0116_add_again_presence_tracking
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0117_expand_admin_permission_scopes"
down_revision = "0116_add_again_presence_tracking"
branch_labels = None
depends_on = None


_LEGACY_TO_NEW: dict[str, tuple[str, ...]] = {
    "showcase.read": ("control.content.read",),
    "showcase.edit": ("control.content.edit",),
    "control.sources.read": ("control.sync.read",),
    "control.sources.edit": ("control.sync.edit",),
    "control.settings.read": (
        "control.payments.read",
        "control.settings_transfer.read",
        "control.site_access.read",
    ),
    "control.settings.edit": (
        "control.payments.edit",
        "control.settings_transfer.edit",
        "control.site_access.edit",
    ),
}

_NEW_PERMISSION_KEYS = {
    permission
    for permissions in _LEGACY_TO_NEW.values()
    for permission in permissions
}
_REMOVED_PERMISSION_KEYS = {"accounts.read", "accounts.edit"}


def _role_permissions(bind) -> list[tuple[int, list[str]]]:
    rows = bind.execute(sa.text("SELECT id, permissions FROM admin_role")).mappings()
    result: list[tuple[int, list[str]]] = []
    for row in rows:
        raw_permissions = row["permissions"]
        permissions = raw_permissions if isinstance(raw_permissions, list) else []
        result.append((int(row["id"]), [str(item) for item in permissions if str(item).strip()]))
    return result


def upgrade() -> None:
    bind = op.get_bind()
    for role_id, permissions in _role_permissions(bind):
        merged = [permission for permission in dict.fromkeys(permissions) if permission not in _REMOVED_PERMISSION_KEYS]
        known = set(merged)
        for legacy, additions in _LEGACY_TO_NEW.items():
            if legacy not in known:
                continue
            for permission in additions:
                if permission not in known:
                    merged.append(permission)
                    known.add(permission)
        if merged != permissions:
            bind.execute(
                sa.text("UPDATE admin_role SET permissions = CAST(:permissions AS jsonb) WHERE id = :role_id"),
                {"permissions": json.dumps(merged), "role_id": role_id},
            )


def downgrade() -> None:
    bind = op.get_bind()
    for role_id, permissions in _role_permissions(bind):
        retained = [permission for permission in permissions if permission not in _NEW_PERMISSION_KEYS]
        if retained != permissions:
            bind.execute(
                sa.text("UPDATE admin_role SET permissions = CAST(:permissions AS jsonb) WHERE id = :role_id"),
                {"permissions": json.dumps(retained), "role_id": role_id},
            )
