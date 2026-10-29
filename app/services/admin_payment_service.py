import secrets
from decimal import Decimal, InvalidOperation

from app.core.config import settings
from app.models.admin_payment import AdminPayment
from app.repositories.admin_payment import PaymentRepository
from app.services.antilopay_client import AntilopayClient, ProviderError


class PaymentError(Exception):
    def __init__(self, detail, code=409):
        self.detail = detail
        self.code = code


class PaymentService:
    def __init__(self, db, client=None):
        self.db = db
        self.repo = PaymentRepository(db)
        self.client = client or AntilopayClient()

    def _check(self, payment):
        return self.client.post(
            "payment/check",
            {"project_identificator": settings.antilopay_project_id, "order_id": payment.order_number},
        )

    @staticmethod
    def _matches(payment, data, *, require_code):
        try:
            return (
                (not require_code or data.get("code") == 0)
                and data.get("order_id") == payment.order_number
                and data.get("merchant_extra") == payment.ownership_token
                and str(data.get("currency", "")).lower() == "rub"
                and Decimal(str(data["original_amount"])) == payment.amount
                and bool(data.get("payment_id"))
                and data.get("status") in {"PENDING", "SUCCESS", "FAIL", "CANCEL", "EXPIRED", "CHARGEBACK", "REVERSED"}
                and (not payment.provider_payment_id or payment.provider_payment_id == data["payment_id"])
            )
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return False

    def _apply(self, payment, data, *, require_code=True):
        if not self._matches(payment, data, require_code=require_code):
            raise PaymentError("Платёж провайдера не совпадает с локальными данными или статус неизвестен", 502)
        payment.provider_payment_id = data["payment_id"]
        payment.payment_url = data.get("payment_url") or payment.payment_url
        payment.status = data["status"]

    def list(self, limit, offset):
        items, total = self.repo.list(limit, offset)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def create(self, payload, idempotency_key):
        from sqlalchemy.exc import IntegrityError

        payment = AdminPayment(
            **payload.model_dump(),
            order_number=self.repo.next_order(),
            idempotency_key=idempotency_key,
            ownership_token=secrets.token_hex(16),
            status="CREATING",
        )
        self.db.add(payment)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise PaymentError("Ключ идемпотентности уже использован; проверьте список платежей") from exc
        payment_id = payment.id
        payment = self.repo.get(payment_id, lock=True)
        if payment.status != "CREATING":
            raise PaymentError("Платёж уже обработан; проверьте его статус")
        try:
            for _ in range(50):
                check = self._check(payment)
                if check["code"] == 0:
                    payment.order_number = self.repo.next_order()
                    self.db.flush()
                    continue
                if check["code"] != 8:
                    raise ProviderError("Не удалось проверить номер у Antilopay")
                data = {
                    "project_identificator": settings.antilopay_project_id,
                    "order_id": payment.order_number,
                    "amount": payment.amount,
                    "currency": "RUB",
                    "product_name": payment.title,
                    "product_type": "goods",
                    "description": payment.description,
                    "customer": {
                        k: v
                        for k, v in {
                            "email": payment.email,
                            "phone": payment.phone,
                            "fullname": payment.customer_name,
                            "address": payment.address,
                        }.items()
                        if v
                    },
                    "merchant_extra": payment.ownership_token,
                }
                if settings.antilopay_success_url:
                    data["success_url"] = settings.antilopay_success_url
                if settings.antilopay_fail_url:
                    data["fail_url"] = settings.antilopay_fail_url
                result = self.client.post("payment/create", data)
                if result["code"] == 5:
                    conflict = self._check(payment)
                    if conflict["code"] not in {0, 8}:
                        raise ProviderError("Не удалось проверить конфликт номера")
                    payment.order_number = self.repo.next_order()
                    self.db.flush()
                    continue
                if result["code"] in {500, 1000}:
                    payment.status = "CREATE_UNKNOWN"
                    self.db.commit()
                    raise PaymentError(
                        f"Результат создания неизвестен; id={payment_id}, выполните refresh",
                        503,
                    )
                if result["code"] != 0:
                    payment.status = "CREATE_REJECTED"
                    self.db.commit()
                    if result["code"] == 12:
                        raise PaymentError("Сумма платежа слишком маленькая для Antilopay", 422)
                    if result["code"] == 20:
                        raise PaymentError("Сумма платежа ниже минимальной суммы Antilopay", 422)
                    raise PaymentError(f"Antilopay отклонил создание (код {result['code']})", 502)
                if not result.get("payment_id") or not result.get("payment_url"):
                    raise ProviderError("Неполный ответ Antilopay")
                payment.provider_payment_id = result["payment_id"]
                payment.payment_url = result["payment_url"]
                payment.status = "PENDING"
                self.db.commit()
                return payment
            payment.status = "CREATE_UNKNOWN"
            self.db.commit()
            raise PaymentError("Результат создания неизвестен; выполните refresh", 503)
        except ProviderError as exc:
            payment.status = "CREATE_UNKNOWN"
            self.db.commit()
            raise PaymentError(f"Результат создания неизвестен; id={payment_id}, выполните refresh", 503) from exc

    def refresh(self, payment_id):
        payment = self.repo.get(payment_id, lock=True)
        if payment is None:
            raise PaymentError("Платёж не найден", 404)
        try:
            data = self._check(payment)
            if data["code"] == 8 and payment.status == "CREATING":
                raise PaymentError("Создание платежа ещё выполняется; повторите refresh позже", 409)
            if data["code"] == 8 and payment.status == "CREATE_UNKNOWN":
                raise PaymentError("Платёж не найден; результат создания неизвестен, повторное создание запрещено", 409)
            if data["code"] != 0:
                raise PaymentError(f"Проверка Antilopay не подтверждена (код {data['code']})", 502)
            self._apply(payment, data)
            self.db.commit()
            return payment
        except ProviderError as exc:
            raise PaymentError("Проверка Antilopay недоступна", 503) from exc

    def callback(self, payload):
        payment = self.repo.by_order(payload.get("order_id"), lock=True)
        if payment is None:
            raise PaymentError("Платёж не найден", 404)
        if not self._matches(payment, payload, require_code=False):
            if payment.status == "CREATING":
                raise PaymentError("Создание платежа ещё выполняется; повторите callback", 409)
            payment.status = "RECONCILIATION_MISMATCH"
            self.db.commit()
            return payment
        self._apply(payment, payload, require_code=False)
        self.db.commit()
        return payment
