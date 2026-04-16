from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.auth import AdminLoginRequest, AdminMeResponse, AdminRefreshRequest, AdminTokenPairResponse
from app.services.auth.admin_auth_service import (
    issue_admin_token_pair,
    require_admin_access,
    verify_admin_credentials,
    verify_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=AdminTokenPairResponse)
def login(payload: AdminLoginRequest):
    if not verify_admin_credentials(payload.login, payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный логин или пароль")
    return AdminTokenPairResponse(**issue_admin_token_pair())


@router.post("/refresh", response_model=AdminTokenPairResponse)
def refresh(payload: AdminRefreshRequest):
    verify_refresh_token(payload.refresh_token)
    return AdminTokenPairResponse(**issue_admin_token_pair())


@router.get("/me", response_model=AdminMeResponse)
def me(claims=Depends(require_admin_access)):
    return AdminMeResponse(login=claims.sub)
