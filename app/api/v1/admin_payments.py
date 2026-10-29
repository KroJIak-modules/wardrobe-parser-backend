import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from app.core.database import SessionLocal, get_db
from app.schemas.admin_payment import PaymentCreate, PaymentList, PaymentResponse
from app.services.admin_payment_service import PaymentError, PaymentService
from app.services.antilopay_client import verify_callback
from app.services.auth.admin_auth_service import require_permission

router = APIRouter(tags=["admin-payments"])


def call(operation, *args):
    try:
        return operation(*args)
    except PaymentError as exc:
        raise HTTPException(status_code=exc.code, detail=exc.detail) from exc


@router.get("/admin/payments", response_model=PaymentList, dependencies=[Depends(require_permission("control.settings.read"))])
def list_payments(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    return PaymentService(db).list(limit, offset)


@router.post("/admin/payments", response_model=PaymentResponse, status_code=201, dependencies=[Depends(require_permission("control.settings.edit"))])
def create_payment(payload: PaymentCreate, idempotency_key: str = Header(min_length=1, max_length=128), db: Session = Depends(get_db)):
    return call(PaymentService(db).create, payload, idempotency_key)


@router.post("/admin/payments/{payment_id}/refresh", response_model=PaymentResponse, dependencies=[Depends(require_permission("control.settings.edit"))])
def refresh_payment(payment_id: int, db: Session = Depends(get_db)):
    return call(PaymentService(db).refresh, payment_id)



@router.post("/payments/antilopay/callback", include_in_schema=False)
async def payment_callback(request: Request, x_apay_callback: str = Header(default=""),
                           x_apay_callback_version: str = Header(default="")):
    body = await request.body()
    if not verify_callback(body, x_apay_callback, x_apay_callback_version):
        raise HTTPException(status_code=401, detail="Неверная подпись")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get("type") != "payment" or not isinstance(payload.get("order_id"), str):
            raise ValueError
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=400, detail="Некорректный callback")
    def process_callback():
        db = SessionLocal()
        try:
            call(PaymentService(db).callback, payload)
            return {"ok": True}
        finally:
            db.close()

    return await run_in_threadpool(process_callback)
