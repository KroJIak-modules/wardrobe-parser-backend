import asyncio
import base64
import threading
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.schemas.admin_payment import PaymentCreate, normalize_phone
from app.services.admin_payment_service import PaymentError, PaymentService
from app.services.antilopay_client import ProviderError, verify_callback
from app.core.config import settings


def payment(status="PENDING"):
    return SimpleNamespace(id=1, order_number="AS-1", ownership_token="owned", amount=Decimal("12.30"),
                           provider_payment_id="p1", payment_url=None, status=status)


def checked(p, status="PENDING"):
    return {"code": 0, "order_id": p.order_number, "merchant_extra": p.ownership_token,
            "currency": "rub", "original_amount": "12.30", "payment_id": "p1", "status": status}


class DB:
    def commit(self):
        pass


class Repo:
    def __init__(self, item):
        self.item = item

    def get(self, payment_id, lock=False):
        return self.item

    def by_order(self, order, lock=False):
        return self.item if self.item.order_number == order else None


class Client:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def post(self, path, payload):
        self.calls.append(path)
        return self.data


def test_payment_readiness_requires_callback_key(monkeypatch):
    from app.api.v1.admin_payments import payment_readiness
    monkeypatch.setattr(settings, "antilopay_secret_id", "id")
    monkeypatch.setattr(settings, "antilopay_secret_key", "key")
    monkeypatch.setattr(settings, "antilopay_project_id", "project")
    monkeypatch.setattr(settings, "antilopay_callback_public_key", "")
    assert payment_readiness() == {"configured": False, "callback_configured": False}


def test_reconciliation_rejects_unowned_or_wrong_amount():
    p = payment()
    service = PaymentService(DB(), Client({}))
    for key, value in [("merchant_extra", "other"), ("original_amount", "12.31"), ("payment_id", "other")]:
        data = checked(p, "SUCCESS")
        data[key] = value
        with pytest.raises(PaymentError):
            service._apply(p, data)
    assert p.status == "PENDING"


def test_callback_signature_is_checked_on_raw_bytes(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setattr(settings, "antilopay_callback_public_key", base64.b64encode(pub).decode())
    body = b'{"status":"SUCCESS"}'
    sig = base64.b64encode(key.sign(body, padding.PKCS1v15(), hashes.SHA256())).decode()
    assert verify_callback(body, sig, "1")
    assert not verify_callback(body + b" ", sig, "1")
    assert not verify_callback(body, sig, "2")


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("88005553535", "+7 8005553535"),
        ("8 (800) 555-35-35", "+7 8005553535"),
        ("+7 800 555 35 35", "+7 8005553535"),
        ("+44 20 7946 0958", "+44 2079460958"),
        ("0044 20 7946 0958", "+44 2079460958"),
    ],
)
def test_phone_accepts_common_local_and_international_formats(raw, normalized):
    assert normalize_phone(raw) == normalized


def test_phone_is_validated_before_antilopay():
    assert PaymentCreate(title="Goods", amount="12.30", phone="8 (800) 555-35-35").phone == "+7 8005553535"
    with pytest.raises(ValueError, match="корректный номер"):
        PaymentCreate(title="Goods", amount="12.30", phone="880055535")


def test_decimal_validation():
    p = PaymentCreate(title="Goods", amount="12.30", email="a@example.org")
    assert p.amount == Decimal("12.30")
    with pytest.raises(ValueError):
        PaymentCreate(title="Goods", amount="12.301", email="a@example.org")


@pytest.mark.parametrize("status", ["CREATING", "CREATE_UNKNOWN"])
def test_missing_unknown_creation_is_not_retried(status):
    p = payment(status)
    client = Client({"code": 8})
    service = PaymentService(DB(), client)
    service.repo = Repo(p)
    for _ in range(2):
        with pytest.raises(PaymentError) as error:
            service.refresh(1)
        assert error.value.code == 409
        assert p.status == status
    assert client.calls == ["payment/check", "payment/check"]


@pytest.mark.parametrize("status", ["CREATING", "CREATE_UNKNOWN"])
def test_unknown_creation_is_reconciled_when_found(status):
    p = payment(status)
    p.provider_payment_id = None
    client = Client(checked(p, "SUCCESS"))
    service = PaymentService(DB(), client)
    service.repo = Repo(p)
    assert service.refresh(1).status == "SUCCESS"
    assert p.provider_payment_id == "p1"
    assert client.calls == ["payment/check"]


def test_unknown_creation_provider_failure_does_not_retry():
    p = payment("CREATE_UNKNOWN")

    class FailedClient(Client):
        def post(self, path, payload):
            self.calls.append(path)
            raise ProviderError()

    client = FailedClient({})
    service = PaymentService(DB(), client)
    service.repo = Repo(p)
    with pytest.raises(PaymentError) as error:
        service.refresh(1)
    assert error.value.code == 503
    assert p.status == "CREATE_UNKNOWN"
    assert client.calls == ["payment/check"]


def test_callback_reconciliation_does_not_block_event_loop(monkeypatch):
    from starlette.requests import Request
    from app.api.v1 import admin_payments

    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(admin_payments, "verify_callback", lambda *args: True)

    class Service:
        def __init__(self, db):
            pass

        def callback(self, payload):
            started.set()
            release.wait(timeout=2)

    monkeypatch.setattr(admin_payments, "PaymentService", Service)

    async def run():
        async def receive():
            return {"type": "http.request", "body": b'{"type":"payment","order_id":"AS-1"}'}

        request = Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)
        task = asyncio.create_task(admin_payments.payment_callback(request, "sig", "1"))
        deadline = time.monotonic() + 1
        while not started.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert started.is_set()
        assert not task.done()
        release.set()
        assert await task == {"ok": True}

    try:
        asyncio.run(run())
    finally:
        release.set()


@pytest.mark.parametrize("code", [500, 1000])
def test_create_server_error_is_unknown(code):
    class CreateDB(DB):
        def add(self, obj):
            obj.id = 1
            self.item = obj

    class CreateRepo:
        def __init__(self, db):
            self.db = db

        def next_order(self):
            return "AS-1"

        def get(self, payment_id, lock=False):
            return self.db.item

    class CreateClient:
        def __init__(self):
            self.calls = []

        def post(self, path, payload):
            self.calls.append(path)
            return {"code": 8 if path == "payment/check" else code}

    db = CreateDB()
    client = CreateClient()
    service = PaymentService(db, client)
    service.repo = CreateRepo(db)
    payload = PaymentCreate(title="Goods", amount="12.30", email="a@example.org")
    with pytest.raises(PaymentError) as error:
        service.create(payload, "unique")
    assert error.value.code == 503
    assert db.item.status == "CREATE_UNKNOWN"
    assert client.calls == ["payment/check", "payment/create"]


@pytest.mark.parametrize("field,value", [
    ("original_amount", "99.99"), ("payment_id", "foreign"),
    ("merchant_extra", "foreign"), ("currency", "usd"),
])
def test_callback_mismatch_is_persisted_without_provider_call(field, value):
    p = payment()
    data = checked(p, "SUCCESS")
    data[field] = value
    client = Client({})
    db = DB()
    db.commits = 0
    db.commit = lambda: setattr(db, "commits", db.commits + 1)
    service = PaymentService(db, client)
    service.repo = Repo(p)
    service.callback(data)
    assert p.status == "RECONCILIATION_MISMATCH"
    assert db.commits == 1
    assert client.calls == []


@pytest.mark.parametrize("status", ["SUCCESS", "FAIL", "CANCEL", "EXPIRED", "CHARGEBACK", "REVERSED"])
def test_valid_callback_applies_provider_status(status):
    p = payment("PENDING")
    client = Client({})
    service = PaymentService(DB(), client)
    service.repo = Repo(p)
    service.callback(checked(p, status))
    assert p.status == status
    assert p.provider_payment_id == "p1"
    assert client.calls == []


def test_mismatch_during_creating_does_not_disrupt_create():
    p = payment("CREATING")
    data = checked(p, "SUCCESS")
    data["original_amount"] = "1.00"
    service = PaymentService(DB(), Client({}))
    service.repo = Repo(p)
    with pytest.raises(PaymentError):
        service.callback(data)
    assert p.status == "CREATING"
