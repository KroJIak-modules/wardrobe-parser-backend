import base64
import json
from decimal import Decimal

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.core.config import settings


class ProviderError(Exception):
    pass


class AntilopayClient:
    def post(self, path: str, payload: dict) -> dict:
        if not all((settings.antilopay_secret_id, settings.antilopay_secret_key, settings.antilopay_project_id)):
            raise ProviderError("Antilopay не настроен")
        # Decimal is encoded as a JSON number without converting through float.
        data = dict(payload)
        amount = data.pop("amount", None)
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if amount is not None:
            body = body[:-1] + ',"amount":' + str(amount) + "}"
        raw = body.encode("utf-8")
        try:
            key = serialization.load_der_private_key(base64.b64decode(settings.antilopay_secret_key, validate=True), password=None)
            signature = base64.b64encode(key.sign(raw, padding.PKCS1v15(), hashes.SHA256())).decode("ascii")
            response = requests.post(
                "https://lk.antilopay.com/api/v1/" + path,
                data=raw,
                headers={"Content-Type": "application/json", "X-Apay-Secret-Id": settings.antilopay_secret_id,
                         "X-Apay-Sign": signature, "X-Apay-Sign-Version": "1"},
                timeout=settings.antilopay_timeout_sec,
            )
            response.raise_for_status()
            data = response.json(parse_float=Decimal)
            if not isinstance(data, dict) or not isinstance(data.get("code"), int):
                raise ValueError("invalid response")
            return data
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise ProviderError("Antilopay недоступен или вернул некорректный ответ") from exc


def verify_callback(body: bytes, signature: str, version: str) -> bool:
    if version != "1" or not signature or not settings.antilopay_callback_public_key:
        return False
    try:
        key = serialization.load_der_public_key(base64.b64decode(settings.antilopay_callback_public_key, validate=True))
        key.verify(base64.b64decode(signature, validate=True), body, padding.PKCS1v15(), hashes.SHA256())
        return True
    except (ValueError, TypeError, AttributeError, InvalidSignature):
        return False
