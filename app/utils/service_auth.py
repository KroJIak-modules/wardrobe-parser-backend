from fastapi import Header, HTTPException

from app.core.config import settings


def verify_service_token(x_service_token: str = Header(default="")) -> None:
    if not settings.service_token:
        return
    if x_service_token != settings.service_token:
        raise HTTPException(status_code=401, detail="Invalid service token")
