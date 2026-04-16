"""Image endpoint served directly by backend (no proxy hop via service)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.media.image_gateway_service import ImageGatewayService


router = APIRouter(tags=["images"])


@router.get(
    "/images/{image_id}",
    summary="Изображение товара",
    description="Проксирует и кэширует изображение. Поддерживает query-параметры w, h, q для ресайза.",
)
def get_image(image_id: int, request: Request, db: Session = Depends(get_db)):
    service = ImageGatewayService(db)
    return service.get_image(image_id=image_id, request=request)
