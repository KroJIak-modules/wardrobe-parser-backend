from __future__ import annotations

import requests

from app.api.v1.products import _probe_service_product
from app.core.exceptions import NotFoundError, ValidationError


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")


def test_probe_service_product_retries_after_temporary_source_failure(monkeypatch) -> None:
    post_calls = 0
    status_calls = {"job-1": 0, "job-2": 0}

    def fake_post(*args, **kwargs):
        nonlocal post_calls
        post_calls += 1
        return _FakeResponse({"job_id": f"job-{post_calls}"})

    def fake_get(url: str, *args, **kwargs):
        if url.endswith("/probe/jobs/job-1"):
            status_calls["job-1"] += 1
            return _FakeResponse({"status": "failed"})
        if url.endswith("/probe/jobs/job-2"):
            status_calls["job-2"] += 1
            return _FakeResponse({"status": "completed"})
        if url.endswith("/probe/jobs/job-1/events"):
            return _FakeResponse(
                {
                    "items": [
                        {
                            "type": "source_progress",
                            "payload": {
                                "stage": "fetch_skip",
                                "fields": {
                                    "reason": "('Connection broken: IncompleteRead(5829 bytes read, 4411 more expected)', IncompleteRead(...))",
                                },
                            },
                        },
                        {
                            "type": "source_finished",
                            "payload": {
                                "error_code": "source_failed",
                                "error_message": "('Connection broken: IncompleteRead(5829 bytes read, 4411 more expected)', IncompleteRead(...))",
                                "error": {"report_errors": ["product_not_found:https://www.grailed.com/listings/97410893"]},
                            },
                        },
                    ],
                }
            )
        if url.endswith("/probe/jobs/job-2/events"):
            return _FakeResponse(
                {
                    "items": [
                        {
                            "type": "product_batch",
                            "payload": {
                                "items": [
                                    {
                                        "url": "https://www.grailed.com/listings/97410893-kapital-kapital-knit-32-red-blue-shirt",
                                        "title": "Knit '32' Red & Blue Shirt",
                                    }
                                ]
                            },
                        }
                    ]
                }
            )
        raise AssertionError(f"Unexpected GET {url}")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("app.api.v1.products.time.sleep", lambda *_args, **_kwargs: None)

    item = _probe_service_product("https://www.grailed.com/listings/97410893-kapital-kapital-knit-32-red-blue-shirt")

    assert item["title"] == "Knit '32' Red & Blue Shirt"
    assert post_calls == 2
    assert status_calls == {"job-1": 1, "job-2": 1}


def test_probe_service_product_returns_human_temporary_error_after_all_retries(monkeypatch) -> None:
    def fake_post(*args, **kwargs):
        call_index = getattr(fake_post, "call_index", 0) + 1
        fake_post.call_index = call_index
        return _FakeResponse({"job_id": f"job-{call_index}"})

    def fake_get(url: str, *args, **kwargs):
        if "/probe/jobs/" in url and not url.endswith("/events"):
            return _FakeResponse({"status": "failed"})
        if url.endswith("/events"):
            return _FakeResponse(
                {
                    "items": [
                        {
                            "type": "source_progress",
                            "payload": {
                                "stage": "fetch_skip",
                                "fields": {
                                    "reason": "('Connection broken: IncompleteRead(5829 bytes read, 4411 more expected)', IncompleteRead(...))",
                                },
                            },
                        },
                        {
                            "type": "source_finished",
                            "payload": {
                                "error_code": "source_failed",
                                "error_message": "('Connection broken: IncompleteRead(5829 bytes read, 4411 more expected)', IncompleteRead(...))",
                            },
                        },
                    ],
                }
            )
        raise AssertionError(f"Unexpected GET {url}")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("app.api.v1.products.time.sleep", lambda *_args, **_kwargs: None)

    try:
        _probe_service_product("https://www.grailed.com/listings/97410893-kapital-kapital-knit-32-red-blue-shirt")
    except ValidationError as exc:
        assert str(exc) == "Источник временно оборвал загрузку страницы товара. Попробуй повторить еще раз."
    else:
        raise AssertionError("ValidationError not raised")


def test_probe_service_product_keeps_not_found_for_real_missing_item(monkeypatch) -> None:
    def fake_post(*args, **kwargs):
        return _FakeResponse({"job_id": "job-1"})

    def fake_get(url: str, *args, **kwargs):
        if url.endswith("/probe/jobs/job-1"):
            return _FakeResponse({"status": "failed"})
        if url.endswith("/probe/jobs/job-1/events"):
            return _FakeResponse(
                {
                    "items": [
                        {
                            "type": "source_finished",
                            "payload": {
                                "error_code": "source_failed",
                                "error_message": "product_not_found:https://www.grailed.com/listings/missing-item",
                                "error": {"report_errors": ["product_not_found:https://www.grailed.com/listings/missing-item"]},
                            },
                        }
                    ]
                }
            )
        raise AssertionError(f"Unexpected GET {url}")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("app.api.v1.products.time.sleep", lambda *_args, **_kwargs: None)

    try:
        _probe_service_product("https://www.grailed.com/listings/missing-item")
    except NotFoundError as exc:
        assert str(exc) == "Товар по URL не найден"
    else:
        raise AssertionError("NotFoundError not raised")


def test_probe_service_product_returns_human_message_when_previous_probe_still_running(monkeypatch) -> None:
    def fake_post(*args, **kwargs):
        return _FakeResponse({"detail": "sync already in progress"}, status_code=409)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("app.api.v1.products.time.sleep", lambda *_args, **_kwargs: None)

    try:
        _probe_service_product("https://www.grailed.com/listings/97410893-kapital-kapital-knit-32-red-blue-shirt")
    except ValidationError as exc:
        assert str(exc) == "Предыдущая проверка ссылки еще не завершилась. Подожди немного и попробуй снова."
    else:
        raise AssertionError("ValidationError not raised")
