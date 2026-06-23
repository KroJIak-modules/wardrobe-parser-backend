from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.core import startup as startup_module


def test_run_db_bootstrap_with_retry_retries_on_transient_operational_error(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []
    sleep_calls: list[float] = []

    def action() -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise OperationalError("select 1", {}, RuntimeError('could not translate host name "postgres" to address'))
        return "ok"

    monkeypatch.setattr(startup_module.time, "sleep", lambda value: sleep_calls.append(float(value)))

    assert startup_module.run_db_bootstrap_with_retry(action, label="bootstrap", initial_delay_sec=0.1, max_delay_sec=0.2) == "ok"
    assert len(attempts) == 2
    assert sleep_calls == [0.1]


def test_run_db_bootstrap_with_retry_does_not_swallow_non_transient_operational_error(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []

    def action() -> None:
        raise OperationalError("select 1", {}, RuntimeError('relation "admin_users" does not exist'))

    monkeypatch.setattr(startup_module.time, "sleep", lambda value: sleep_calls.append(float(value)))

    with pytest.raises(OperationalError):
        startup_module.run_db_bootstrap_with_retry(action, label="bootstrap", initial_delay_sec=0.1, max_delay_sec=0.2)

    assert sleep_calls == []
