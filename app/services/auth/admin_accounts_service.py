from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AdminRole, AdminUser
from app.repositories import AdminRoleRepository, AdminUserRepository
from app.schemas.auth import (
    AdminRoleCreateRequest,
    AdminRoleResponse,
    AdminRoleUpdateRequest,
    AdminUserCreateRequest,
    AdminUserPasswordUpdateRequest,
    AdminUserResponse,
    AdminUserUpdateRequest,
)
from app.services.auth.passwords import hash_password, verify_password
from app.services.auth.permissions import normalize_permission_list


class AdminAccountsValidationError(ValueError):
    """Client-side validation error for admin accounts flows."""


@dataclass(frozen=True)
class BootstrapResult:
    user: AdminUser
    created: bool


def _role_payload(role: AdminRole) -> AdminRoleResponse:
    return AdminRoleResponse(
        id=int(role.id),
        name=str(role.name),
        description=role.description,
        permissions=normalize_permission_list(role.permissions),
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


def _user_permissions(user: AdminUser) -> list[str]:
    if bool(user.is_superuser):
        return ["*"]
    role = user.role
    if role is None:
        return []
    return normalize_permission_list(role.permissions)


def _user_payload(user: AdminUser) -> AdminUserResponse:
    role = user.role
    return AdminUserResponse(
        id=int(user.id),
        login=str(user.login),
        is_active=bool(user.is_active),
        is_superuser=bool(user.is_superuser),
        role_id=int(role.id) if role is not None else None,
        role_name=str(role.name) if role is not None else None,
        permissions=_user_permissions(user),
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


class AdminAccountsService:
    def __init__(self, db: Session):
        self.db = db
        self.roles = AdminRoleRepository(db)
        self.users = AdminUserRepository(db)

    def ensure_superadmin_user(self) -> BootstrapResult:
        login = str(settings.admin_superuser_login or "").strip()
        password = str(settings.admin_superuser_password or "")
        if not login:
            raise AdminAccountsValidationError("ADMIN_SUPERUSER_LOGIN не задан")
        if not password:
            raise AdminAccountsValidationError("ADMIN_SUPERUSER_PASSWORD не задан")
        existing = self.users.get_by_login(login)
        if existing is None:
            password_hash = hash_password(password)
            entity = self.users.create(
                login=login,
                password_hash=password_hash,
                is_active=True,
                is_superuser=True,
                role_id=None,
            )
            self.db.commit()
            self.db.refresh(entity)
            return BootstrapResult(user=entity, created=True)
        changed = False
        if not existing.is_superuser:
            existing.is_superuser = True
            changed = True
        if not existing.is_active:
            existing.is_active = True
            changed = True
        # Keep superadmin password in sync with env value.
        if not verify_password(password, str(existing.password_hash or "")):
            existing.password_hash = hash_password(password)
            changed = True
        if changed:
            self.db.commit()
            self.db.refresh(existing)
        return BootstrapResult(user=existing, created=False)

    def list_roles(self) -> list[AdminRoleResponse]:
        return [_role_payload(item) for item in self.roles.list_all()]

    def create_role(self, payload: AdminRoleCreateRequest) -> AdminRoleResponse:
        name = str(payload.name or "").strip()
        if not name:
            raise AdminAccountsValidationError("Название роли обязательно")
        if self.roles.get_by_name(name) is not None:
            raise AdminAccountsValidationError("Роль с таким названием уже существует")
        entity = self.roles.create(
            name=name,
            description=(payload.description or None),
            permissions=normalize_permission_list(payload.permissions),
        )
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise AdminAccountsValidationError("Не удалось создать роль: нарушена уникальность") from exc
        self.db.refresh(entity)
        return _role_payload(entity)

    def update_role(self, role_id: int, payload: AdminRoleUpdateRequest) -> AdminRoleResponse:
        role = self.roles.get_by_id(int(role_id))
        if role is None:
            raise AdminAccountsValidationError("Роль не найдена")
        name = str(payload.name or "").strip()
        if not name:
            raise AdminAccountsValidationError("Название роли обязательно")
        existing = self.roles.get_by_name(name)
        if existing is not None and int(existing.id) != int(role.id):
            raise AdminAccountsValidationError("Роль с таким названием уже существует")
        role.name = name
        role.description = payload.description or None
        role.permissions = normalize_permission_list(payload.permissions)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise AdminAccountsValidationError("Не удалось обновить роль") from exc
        self.db.refresh(role)
        return _role_payload(role)

    def delete_role(self, role_id: int) -> None:
        role = self.roles.get_by_id(int(role_id))
        if role is None:
            raise AdminAccountsValidationError("Роль не найдена")
        attached_users = self.users.query().filter(AdminUser.role_id == int(role.id)).count()
        if attached_users > 0:
            raise AdminAccountsValidationError("Нельзя удалить роль, к которой привязаны пользователи")
        self.db.delete(role)
        self.db.commit()

    def list_users(self) -> list[AdminUserResponse]:
        return [_user_payload(item) for item in self.users.list_all()]

    def _resolve_role_or_raise(self, role_id: int | None) -> AdminRole | None:
        if role_id is None:
            return None
        role = self.roles.get_by_id(int(role_id))
        if role is None:
            raise AdminAccountsValidationError("Роль не найдена")
        return role

    def create_user(self, payload: AdminUserCreateRequest) -> AdminUserResponse:
        login = str(payload.login or "").strip()
        if not login:
            raise AdminAccountsValidationError("Логин обязателен")
        if self.users.get_by_login(login) is not None:
            raise AdminAccountsValidationError("Пользователь с таким логином уже существует")
        role = self._resolve_role_or_raise(payload.role_id)
        entity = self.users.create(
            login=login,
            password_hash=hash_password(payload.password),
            is_active=bool(payload.is_active),
            is_superuser=False,
            role_id=int(role.id) if role is not None else None,
        )
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise AdminAccountsValidationError("Не удалось создать пользователя") from exc
        entity = self.users.get_with_role(int(entity.id))
        if entity is None:
            raise AdminAccountsValidationError("Не удалось загрузить созданного пользователя")
        return _user_payload(entity)

    def update_user(self, user_id: int, payload: AdminUserUpdateRequest, *, actor_user_id: int) -> AdminUserResponse:
        entity = self.users.get_with_role(int(user_id))
        if entity is None:
            raise AdminAccountsValidationError("Пользователь не найден")
        if entity.is_superuser:
            if int(entity.id) == int(actor_user_id) and not payload.is_active:
                raise AdminAccountsValidationError("Нельзя отключить самого себя")
            # Superadmin role stays system-owned.
            entity.role_id = None
        else:
            role = self._resolve_role_or_raise(payload.role_id)
            entity.role_id = int(role.id) if role is not None else None
        entity.login = str(payload.login or "").strip()
        if not entity.login:
            raise AdminAccountsValidationError("Логин обязателен")
        entity.is_active = bool(payload.is_active)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise AdminAccountsValidationError("Не удалось обновить пользователя") from exc
        entity = self.users.get_with_role(int(entity.id))
        if entity is None:
            raise AdminAccountsValidationError("Не удалось загрузить пользователя")
        return _user_payload(entity)

    def update_user_password(self, user_id: int, payload: AdminUserPasswordUpdateRequest) -> None:
        entity = self.users.get_by_id(int(user_id))
        if entity is None:
            raise AdminAccountsValidationError("Пользователь не найден")
        entity.password_hash = hash_password(payload.password)
        self.db.commit()

    def delete_user(self, user_id: int, *, actor_user_id: int) -> None:
        entity = self.users.get_by_id(int(user_id))
        if entity is None:
            raise AdminAccountsValidationError("Пользователь не найден")
        if int(entity.id) == int(actor_user_id):
            raise AdminAccountsValidationError("Нельзя удалить самого себя")
        if entity.is_superuser:
            others = (
                self.users.query()
                .filter(AdminUser.id != int(entity.id))
                .filter(AdminUser.is_superuser.is_(True))
                .filter(AdminUser.is_active.is_(True))
                .count()
            )
            if others <= 0:
                raise AdminAccountsValidationError("Нельзя удалить последнего активного суперадмина")
        self.db.delete(entity)
        self.db.commit()
