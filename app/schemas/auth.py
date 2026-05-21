from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.services.auth.permissions import ALL_PERMISSION_KEYS, PERMISSION_SCOPES


class AdminLoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class AdminSessionResponse(BaseModel):
    token_type: str = "bearer"
    access_expires_in: int
    refresh_expires_in: int


class PermissionScopeResponse(BaseModel):
    key: str
    read: str
    edit: str


class AdminMeResponse(BaseModel):
    user_id: int
    login: str
    role_name: str | None = None
    is_superuser: bool = False
    is_active: bool = True
    permissions: list[str] = []


class AdminRoleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    permissions: list[str] = Field(default_factory=list)

    @field_validator("permissions")
    @classmethod
    def _validate_permissions(cls, value: list[str]) -> list[str]:
        normalized = []
        seen = set()
        for permission in value:
            key = str(permission or "").strip()
            if not key:
                continue
            if key not in ALL_PERMISSION_KEYS:
                raise ValueError(f"Unknown permission: {key}")
            if key in seen:
                continue
            seen.add(key)
            normalized.append(key)
        return normalized


class AdminRoleUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    permissions: list[str] = Field(default_factory=list)

    @field_validator("permissions")
    @classmethod
    def _validate_permissions(cls, value: list[str]) -> list[str]:
        normalized = []
        seen = set()
        for permission in value:
            key = str(permission or "").strip()
            if not key:
                continue
            if key not in ALL_PERMISSION_KEYS:
                raise ValueError(f"Unknown permission: {key}")
            if key in seen:
                continue
            seen.add(key)
            normalized.append(key)
        return normalized


class AdminRoleResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    permissions: list[str]
    created_at: datetime
    updated_at: datetime


class AdminUserCreateRequest(BaseModel):
    login: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=256)
    role_id: int | None = None
    is_active: bool = True


class AdminUserUpdateRequest(BaseModel):
    login: str = Field(min_length=1, max_length=128)
    role_id: int | None = None
    is_active: bool


class AdminUserPasswordUpdateRequest(BaseModel):
    password: str = Field(min_length=8, max_length=256)


class AdminUserResponse(BaseModel):
    id: int
    login: str
    is_active: bool
    is_superuser: bool
    role_id: int | None = None
    role_name: str | None = None
    permissions: list[str]
    created_at: datetime
    updated_at: datetime


class AdminAccountsBootstrapResponse(BaseModel):
    scopes: list[PermissionScopeResponse]
    roles: list[AdminRoleResponse]
    users: list[AdminUserResponse]


def permission_scopes_payload() -> list[PermissionScopeResponse]:
    return [
        PermissionScopeResponse(key=item.key, read=item.read, edit=item.edit)
        for item in PERMISSION_SCOPES
    ]
