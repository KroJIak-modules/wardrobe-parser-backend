from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import phonenumbers
from phonenumbers import NumberParseException


def normalize_phone(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    raw = value.strip()
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    elif raw.startswith("7") and len("".join(character for character in raw if character.isdigit())) == 11:
        raw = "+" + raw
    try:
        parsed = phonenumbers.parse(raw, "RU" if not raw.startswith("+") else None)
    except NumberParseException as exc:
        raise ValueError("Укажите корректный номер телефона") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("Укажите корректный номер телефона")
    return f"+{parsed.country_code} {parsed.national_number}"


class PaymentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)
    description: str = Field(default="", max_length=2000)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    customer_name: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=2000)

    @field_validator("phone")
    @classmethod
    def normalize_phone_field(cls, value: str | None) -> str | None:
        return normalize_phone(value)

    @model_validator(mode="after")
    def validate_customer(self):
        if not self.title.strip() or not (self.email and self.email.strip() or self.phone and self.phone.strip()):
            raise ValueError("Укажите название и email или телефон")
        return self


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_number: str
    title: str
    amount: Decimal
    description: str
    email: str | None
    phone: str | None
    customer_name: str | None
    address: str | None
    status: str
    payment_url: str | None
    created_at: datetime


class PaymentReadiness(BaseModel):
    configured: bool
    callback_configured: bool


class PaymentList(BaseModel):
    items: list[PaymentResponse]
    total: int
    limit: int
    offset: int
