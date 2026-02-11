from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.parser import ParserBatchIn, ParserBatchResponse
from app.services.product_service import ProductService
from app.utils.service_auth import verify_service_token

router = APIRouter()


@router.post(
    "/items",
    response_model=ParserBatchResponse,
    summary="Ingest parser items",
    dependencies=[Depends(verify_service_token)],
)
def ingest_items(payload: ParserBatchIn, db: Session = Depends(get_db)) -> ParserBatchResponse:
    created, updated = ProductService.upsert_from_parser(db, [item.model_dump() for item in payload.items])
    return ParserBatchResponse(created=created, updated=updated)
