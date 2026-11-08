from types import SimpleNamespace

import pytest
import requests

from app.services.settings.bybit_rate_provider import (
    BybitMarketUnavailable,
    BybitP2PRateProvider,
    BybitTransportUnavailable,
)
from app.services.settings.pricing_service import PricingSettingsService


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _empty_payload() -> dict:
    return {"ret_code": 0, "ret_msg": "SUCCESS", "result": {"count": 0, "items": []}}


def _valid_payload() -> dict:
    return {
        "ret_code": 0,
        "ret_msg": "SUCCESS",
        "result": {
            "count": 1,
            "items": [
                {
                    "id": "ad-1",
                    "nickName": "seller",
                    "price": "100.5",
                    "minAmount": "5000",
                    "maxAmount": "200000",
                    "quantity": "3000",
                }
            ],
        },
    }


def test_provider_retries_empty_market_before_accepting_valid_ads(monkeypatch) -> None:
    responses = [_Response(_empty_payload()), _Response(_valid_payload())]
    calls: list[dict] = []
    monkeypatch.setattr(
        "app.services.settings.bybit_rate_provider.requests.post",
        lambda *_args, **kwargs: (calls.append(kwargs) or responses.pop(0)),
    )
    monkeypatch.setattr("app.services.settings.bybit_rate_provider.time.sleep", lambda _delay: None)

    snapshot = BybitP2PRateProvider._fetch_snapshot(
        fiat="RUB",
        asset="USDT",
        timeout_sec=8,
        ads_limit=60,
        bucket_step_usdt=50,
        bucket_max_usdt=100,
        outlier_max_deviation_ratio=0.08,
    )

    assert len(calls) == 2
    assert snapshot.ads_total == 1
    assert snapshot.representative_rate == 100.5
    assert calls[0]["headers"]["Origin"] == "https://www.bybit.com"


def test_provider_classifies_repeated_empty_market_and_short_caches_error(monkeypatch) -> None:
    calls = 0

    def fake_post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Response(_empty_payload())

    monkeypatch.setattr("app.services.settings.bybit_rate_provider.requests.post", fake_post)
    monkeypatch.setattr("app.services.settings.bybit_rate_provider.time.sleep", lambda _delay: None)
    BybitP2PRateProvider._cache.clear()

    try:
        with pytest.raises(BybitMarketUnavailable, match="no USDT/RUB offers"):
            BybitP2PRateProvider.get_snapshot(
                fiat="RUB",
                asset="USDT",
                timeout_sec=8,
                cache_sec=300,
                ads_limit=60,
            )

        assert calls == 3
        cached_error = BybitP2PRateProvider.get_cached_error(fiat="RUB", asset="USDT")
        assert cached_error is not None and "no USDT/RUB offers" in cached_error

        with pytest.raises(BybitMarketUnavailable, match="no USDT/RUB offers"):
            BybitP2PRateProvider.get_snapshot(
                fiat="RUB",
                asset="USDT",
                timeout_sec=8,
                cache_sec=300,
                ads_limit=60,
            )
        assert calls == 3
    finally:
        BybitP2PRateProvider._cache.clear()


def test_provider_treats_invalid_offer_rows_as_transport_failure(monkeypatch) -> None:
    invalid_payload = {
        "ret_code": 0,
        "ret_msg": "SUCCESS",
        "result": {"count": 1, "items": [{"id": "ad-1", "price": "not-a-number"}]},
    }
    monkeypatch.setattr(
        "app.services.settings.bybit_rate_provider.requests.post",
        lambda *_args, **_kwargs: _Response(invalid_payload),
    )
    monkeypatch.setattr("app.services.settings.bybit_rate_provider.time.sleep", lambda _delay: None)

    with pytest.raises(BybitTransportUnavailable, match="no valid prices or limits"):
        BybitP2PRateProvider._fetch_snapshot(
            fiat="RUB",
            asset="USDT",
            timeout_sec=8,
            ads_limit=60,
            bucket_step_usdt=50,
            bucket_max_usdt=100,
            outlier_max_deviation_ratio=0.08,
        )


def test_provider_retries_transport_errors_with_bounded_attempts(monkeypatch) -> None:
    calls = 0

    def raise_timeout(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise requests.Timeout()

    monkeypatch.setattr("app.services.settings.bybit_rate_provider.requests.post", raise_timeout)
    monkeypatch.setattr("app.services.settings.bybit_rate_provider.time.sleep", lambda _delay: None)

    with pytest.raises(BybitTransportUnavailable, match="request failed: Timeout"):
        BybitP2PRateProvider._fetch_snapshot(
            fiat="RUB",
            asset="USDT",
            timeout_sec=8,
            ads_limit=60,
            bucket_step_usdt=50,
            bucket_max_usdt=100,
            outlier_max_deviation_ratio=0.08,
        )

    assert calls == 3


def test_pricing_marks_empty_market_without_replacing_saved_rate(monkeypatch) -> None:
    entity = SimpleNamespace(bybit_last_error=None, usdt_to_rub_rate=99.0)

    def raise_empty(**_kwargs):
        raise BybitMarketUnavailable("Bybit P2P has no USDT/RUB offers at the moment")

    monkeypatch.setattr(BybitP2PRateProvider, "get_snapshot", raise_empty)

    changed, status, snapshot, error = PricingSettingsService._refresh_bybit_rate(entity)

    assert changed is True
    assert status == "fallback_market"
    assert snapshot is None
    assert error == entity.bybit_last_error
    assert entity.usdt_to_rub_rate == 99.0
