from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.auth import (
    AdminAccountsBootstrapResponse,
    AdminLoginRequest,
    AdminMeResponse,
    AdminRoleCreateRequest,
    AdminRoleResponse,
    AdminRoleUpdateRequest,
    AdminSessionResponse,
    AdminUserCreateRequest,
    AdminUserPasswordUpdateRequest,
    AdminUserResponse,
    AdminUserUpdateRequest,
    permission_scopes_payload,
)
from app.services.auth.permissions import ALL_PERMISSION_KEYS
from app.services.auth.login_rate_limiter import LoginRateLimiter
from app.services.auth.admin_accounts_service import AdminAccountsService, AdminAccountsValidationError
from app.services.auth.admin_auth_service import (
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    issue_admin_token_pair,
    require_admin_access,
    require_superadmin,
    verify_admin_credentials,
    verify_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])
_login_rate_limiter = LoginRateLimiter()


def _set_auth_cookies(response: Response, token_pair: dict[str, object]) -> None:
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=str(token_pair["access_token"]),
        httponly=True,
        secure=settings.admin_auth_cookie_secure,
        samesite="lax",
        max_age=int(token_pair["access_expires_in"]),
        path="/",
    )
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=str(token_pair["refresh_token"]),
        httponly=True,
        secure=settings.admin_auth_cookie_secure,
        samesite="lax",
        max_age=int(token_pair["refresh_expires_in"]),
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(key=ACCESS_COOKIE_NAME, path="/")
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path="/")


def _check_login_rate_limit(client_key: str) -> None:
    if _login_rate_limiter.is_limited(client_key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Слишком много попыток входа. Повторите позже.")


def _register_failed_attempt(client_key: str) -> None:
    _login_rate_limiter.register_failed_attempt(client_key)


@router.post("/login", response_model=AdminSessionResponse)
def login(payload: AdminLoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    client_key = request.client.host if request.client else "unknown"
    _check_login_rate_limit(client_key)
    user = verify_admin_credentials(db, payload.login, payload.password)
    if user is None:
        _register_failed_attempt(client_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный логин или пароль")
    token_pair = issue_admin_token_pair(user_id=int(user.id), login=str(user.login))
    _set_auth_cookies(response, token_pair)
    return AdminSessionResponse(
        token_type="bearer",
        access_expires_in=int(token_pair["access_expires_in"]),
        refresh_expires_in=int(token_pair["refresh_expires_in"]),
    )


@router.post("/refresh", response_model=AdminSessionResponse)
def refresh(
    response: Response,
    db: Session = Depends(get_db),
    refresh_cookie_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
):
    refresh_token = refresh_cookie_token
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется refresh-токен")
    context = verify_refresh_token(refresh_token, db)
    token_pair = issue_admin_token_pair(user_id=int(context.user.id), login=str(context.user.login))
    _set_auth_cookies(response, token_pair)
    return AdminSessionResponse(
        token_type="bearer",
        access_expires_in=int(token_pair["access_expires_in"]),
        refresh_expires_in=int(token_pair["refresh_expires_in"]),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response):
    _clear_auth_cookies(response)
    return None


@router.get("/me", response_model=AdminMeResponse)
def me(context=Depends(require_admin_access)):
    role = context.user.role
    permissions = sorted(ALL_PERMISSION_KEYS) if context.is_superuser else sorted(context.permissions)
    return AdminMeResponse(
        user_id=int(context.user.id),
        login=str(context.user.login),
        role_name=(str(role.name) if role is not None else "superadmin" if context.is_superuser else None),
        is_superuser=context.is_superuser,
        is_active=bool(context.user.is_active),
        permissions=permissions,
    )


@router.get("/accounts/bootstrap", response_model=AdminAccountsBootstrapResponse)
def accounts_bootstrap(
    db: Session = Depends(get_db),
    _: object = Depends(require_superadmin),
):
    service = AdminAccountsService(db)
    return AdminAccountsBootstrapResponse(
        scopes=permission_scopes_payload(),
        roles=service.list_roles(),
        users=service.list_users(),
    )


@router.get("/accounts/roles", response_model=list[AdminRoleResponse])
def list_roles(db: Session = Depends(get_db), _: object = Depends(require_superadmin)):
    return AdminAccountsService(db).list_roles()


@router.post("/accounts/roles", response_model=AdminRoleResponse)
def create_role(payload: AdminRoleCreateRequest, db: Session = Depends(get_db), _: object = Depends(require_superadmin)):
    try:
        return AdminAccountsService(db).create_role(payload)
    except AdminAccountsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/accounts/roles/{role_id}", response_model=AdminRoleResponse)
def update_role(role_id: int, payload: AdminRoleUpdateRequest, db: Session = Depends(get_db), _: object = Depends(require_superadmin)):
    try:
        return AdminAccountsService(db).update_role(role_id, payload)
    except AdminAccountsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/accounts/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(role_id: int, db: Session = Depends(get_db), _: object = Depends(require_superadmin)):
    try:
        AdminAccountsService(db).delete_role(role_id)
    except AdminAccountsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return None


@router.get("/accounts/users", response_model=list[AdminUserResponse])
def list_users(db: Session = Depends(get_db), _: object = Depends(require_superadmin)):
    return AdminAccountsService(db).list_users()


@router.post("/accounts/users", response_model=AdminUserResponse)
def create_user(payload: AdminUserCreateRequest, db: Session = Depends(get_db), _: object = Depends(require_superadmin)):
    try:
        return AdminAccountsService(db).create_user(payload)
    except AdminAccountsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/accounts/users/{user_id}", response_model=AdminUserResponse)
def update_user(
    user_id: int,
    payload: AdminUserUpdateRequest,
    db: Session = Depends(get_db),
    context=Depends(require_superadmin),
):
    try:
        return AdminAccountsService(db).update_user(user_id, payload, actor_user_id=int(context.user.id))
    except AdminAccountsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/accounts/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def update_user_password(
    user_id: int,
    payload: AdminUserPasswordUpdateRequest,
    db: Session = Depends(get_db),
    _: object = Depends(require_superadmin),
):
    try:
        AdminAccountsService(db).update_user_password(user_id, payload)
    except AdminAccountsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return None


@router.delete("/accounts/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db), context=Depends(require_superadmin)):
    try:
        AdminAccountsService(db).delete_user(user_id, actor_user_id=int(context.user.id))
    except AdminAccountsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return None
