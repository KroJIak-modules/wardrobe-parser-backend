"""Repositories for admin auth / RBAC entities."""

from __future__ import annotations

from sqlalchemy.orm import Session, joinedload

from app.models import AdminRole, AdminUser
from app.repositories.base import BaseRepository


class AdminRoleRepository(BaseRepository[AdminRole]):
    def __init__(self, session: Session):
        super().__init__(session, AdminRole)

    def list_all(self) -> list[AdminRole]:
        return self.query().order_by(AdminRole.created_at.asc(), AdminRole.id.asc()).all()

    def get_by_name(self, name: str) -> AdminRole | None:
        normalized = str(name or "").strip()
        if not normalized:
            return None
        return self.query().filter(AdminRole.name == normalized).first()


class AdminUserRepository(BaseRepository[AdminUser]):
    def __init__(self, session: Session):
        super().__init__(session, AdminUser)

    def list_all(self) -> list[AdminUser]:
        return (
            self.query()
            .options(joinedload(AdminUser.role))
            .order_by(AdminUser.created_at.asc(), AdminUser.id.asc())
            .all()
        )

    def get_by_login(self, login: str) -> AdminUser | None:
        normalized = str(login or "").strip()
        if not normalized:
            return None
        return (
            self.query()
            .options(joinedload(AdminUser.role))
            .filter(AdminUser.login == normalized)
            .first()
        )

    def get_with_role(self, user_id: int) -> AdminUser | None:
        return (
            self.query()
            .options(joinedload(AdminUser.role))
            .filter(AdminUser.id == int(user_id))
            .first()
        )
