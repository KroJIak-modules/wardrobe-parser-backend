from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status

from app.schemas.auth import AdminLoginRequest, AdminMeResponse, AdminSessionResponse
from app.core.config import settings
from app.services.auth.login_rate_limiter import LoginRateLimiter
from app.services.auth.admin_auth_service import (
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    issue_admin_token_pair,
    require_admin_access,
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
def login(payload: AdminLoginRequest, request: Request, response: Response):
    client_key = request.client.host if request.client else "unknown"
    _check_login_rate_limit(client_key)
    if not verify_admin_credentials(payload.login, payload.password):
        _register_failed_attempt(client_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный логин или пароль")
    token_pair = issue_admin_token_pair()
    _set_auth_cookies(response, token_pair)
    return AdminSessionResponse(
        token_type="bearer",
        access_expires_in=int(token_pair["access_expires_in"]),
        refresh_expires_in=int(token_pair["refresh_expires_in"]),
    )


@router.post("/refresh", response_model=AdminSessionResponse)
def refresh(
    response: Response,
    refresh_cookie_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
):
    refresh_token = refresh_cookie_token
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется refresh-токен")
    verify_refresh_token(refresh_token)
    token_pair = issue_admin_token_pair()
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
def me(claims=Depends(require_admin_access)):
    return AdminMeResponse(login=claims.sub)
