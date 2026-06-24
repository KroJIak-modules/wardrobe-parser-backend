from app.core.config import Settings


def test_admin_token_ttl_accepts_numeric_seconds() -> None:
    settings = Settings(
        ADMIN_ACCESS_TOKEN_TTL_SEC="86400",
        ADMIN_REFRESH_TOKEN_TTL_SEC="604800",
    )

    assert settings.admin_access_token_ttl_sec == 86400
    assert settings.admin_refresh_token_ttl_sec == 604800


def test_admin_token_ttl_accepts_day_and_week_suffixes() -> None:
    settings = Settings(
        ADMIN_ACCESS_TOKEN_TTL_SEC="1d",
        ADMIN_REFRESH_TOKEN_TTL_SEC="1w",
    )

    assert settings.admin_access_token_ttl_sec == 86400
    assert settings.admin_refresh_token_ttl_sec == 604800


def test_admin_token_ttl_accepts_other_supported_duration_suffixes() -> None:
    settings = Settings(
        ADMIN_ACCESS_TOKEN_TTL_SEC="2h",
        ADMIN_REFRESH_TOKEN_TTL_SEC="90m",
    )

    assert settings.admin_access_token_ttl_sec == 7200
    assert settings.admin_refresh_token_ttl_sec == 5400


def test_bybit_worker_interval_accepts_duration_suffixes() -> None:
    settings = Settings(PRICING_BYBIT_WORKER_INTERVAL_SEC="3h")

    assert settings.pricing_bybit_worker_interval_sec == 10800
