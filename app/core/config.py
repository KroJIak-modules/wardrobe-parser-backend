from pathlib import Path
import secrets
from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_backend_root = Path(__file__).resolve().parents[2]
_repo_root = _backend_root.parent
_env_file = _repo_root / ".env"


class Settings(BaseSettings):
    database_url: Optional[str] = Field(default=None, validation_alias="DATABASE_URL")
    postgres_user: str = Field(default="postgres", validation_alias="POSTGRES_USER")
    postgres_password: str = Field(default="", validation_alias="POSTGRES_PASSWORD")
    postgres_host: str = Field(default="localhost", validation_alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, validation_alias="POSTGRES_PORT")
    postgres_db: str = Field(default="wardrobe", validation_alias="POSTGRES_DB")
    cors_allowed_origins: str = Field(default="", validation_alias="CORS_ALLOWED_ORIGINS")
    service_base_url: str = Field(default="http://service:8000", validation_alias="SERVICE_BASE_URL")
    service_proxy_connect_timeout_sec: float = Field(default=10.0, validation_alias="SERVICE_PROXY_CONNECT_TIMEOUT_SEC")
    service_proxy_read_timeout_sec: float = Field(default=120.0, validation_alias="SERVICE_PROXY_READ_TIMEOUT_SEC")
    redis_url: str = Field(default="redis://redis:6379/0", validation_alias="REDIS_URL")
    weight_default_fallback_grams: int = Field(default=1000, validation_alias="DEFAULT_FALLBACK_WEIGHT_GRAMS")
    dedup_scan_limit: int = Field(default=2000, ge=10, le=100000, validation_alias="DEDUP_SCAN_LIMIT")
    dedup_score_threshold: float = Field(default=0.55, ge=0.0, le=1.0, validation_alias="DEDUP_SCORE_THRESHOLD")
    dedup_title_match_weight: float = Field(default=0.55, ge=0.0, le=1.0, validation_alias="DEDUP_TITLE_MATCH_WEIGHT")
    dedup_vendor_match_weight: float = Field(default=0.25, ge=0.0, le=1.0, validation_alias="DEDUP_VENDOR_MATCH_WEIGHT")
    dedup_price_close_weight: float = Field(default=0.15, ge=0.0, le=1.0, validation_alias="DEDUP_PRICE_CLOSE_WEIGHT")
    dedup_handle_match_weight: float = Field(default=0.2, ge=0.0, le=1.0, validation_alias="DEDUP_HANDLE_MATCH_WEIGHT")
    dedup_price_diff_ratio_limit: float = Field(default=0.08, ge=0.0, le=1.0, validation_alias="DEDUP_PRICE_DIFF_RATIO_LIMIT")
    dedup_score_cap: float = Field(default=0.99, ge=0.0, le=1.0, validation_alias="DEDUP_SCORE_CAP")
    dedup_candidates_default_limit: int = Field(default=30, ge=1, le=1000, validation_alias="DEDUP_CANDIDATES_DEFAULT_LIMIT")
    dedup_candidates_max_limit: int = Field(default=200, ge=1, le=5000, validation_alias="DEDUP_CANDIDATES_MAX_LIMIT")
    dedup_bucket_product_cap: int = Field(default=50, ge=2, le=500, validation_alias="DEDUP_BUCKET_PRODUCT_CAP")
    dedup_pair_scan_cap: int = Field(default=25000, ge=100, le=1_000_000, validation_alias="DEDUP_PAIR_SCAN_CAP")
    pricing_bybit_rate_auto_enabled: bool = Field(default=True, validation_alias="PRICING_BYBIT_RATE_AUTO_ENABLED")
    pricing_bybit_rate_cache_sec: int = Field(default=300, ge=30, le=86400, validation_alias="PRICING_BYBIT_RATE_CACHE_SEC")
    pricing_bybit_rate_timeout_sec: float = Field(default=12.0, ge=1.0, le=60.0, validation_alias="PRICING_BYBIT_RATE_TIMEOUT_SEC")
    pricing_bybit_fiat: str = Field(default="RUB", validation_alias="PRICING_BYBIT_FIAT")
    pricing_bybit_asset: str = Field(default="USDT", validation_alias="PRICING_BYBIT_ASSET")
    pricing_bybit_ads_limit: int = Field(default=60, ge=10, le=200, validation_alias="PRICING_BYBIT_ADS_LIMIT")
    pricing_bybit_bucket_step_usdt: int = Field(default=50, ge=10, le=1000, validation_alias="PRICING_BYBIT_BUCKET_STEP_USDT")
    pricing_bybit_bucket_max_usdt: int = Field(default=5000, ge=50, le=20000, validation_alias="PRICING_BYBIT_BUCKET_MAX_USDT")
    pricing_bybit_outlier_max_deviation_ratio: float = Field(
        default=0.08,
        ge=0.0,
        le=0.5,
        validation_alias="PRICING_BYBIT_OUTLIER_MAX_DEVIATION_RATIO",
    )
    pricing_bybit_worker_interval_sec: int = Field(
        default=10800,
        ge=30,
        le=86400,
        validation_alias="PRICING_BYBIT_WORKER_INTERVAL_SEC",
    )
    admin_superuser_login: str = Field(default="admin", validation_alias="ADMIN_SUPERUSER_LOGIN")
    admin_superuser_password: str = Field(default="", validation_alias="ADMIN_SUPERUSER_PASSWORD")
    admin_access_token_ttl_sec: int = Field(default=86_400, ge=300, le=2_592_000, validation_alias="ADMIN_ACCESS_TOKEN_TTL_SEC")
    admin_refresh_token_ttl_sec: int = Field(default=604_800, ge=3600, le=7_776_000, validation_alias="ADMIN_REFRESH_TOKEN_TTL_SEC")
    admin_token_secret: str = Field(default="", validation_alias="ADMIN_TOKEN_SECRET")
    admin_auth_cookie_secure: bool = Field(default=False, validation_alias="ADMIN_AUTH_COOKIE_SECURE")

    @model_validator(mode="after")
    def build_database_url(self) -> "Settings":
        if not self.database_url:
            url = (
                f"postgresql://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
            object.__setattr__(self, "database_url", url)
        if not self.admin_superuser_password:
            # Allow utility workers to run without admin credentials.
            # API container still gets explicit ADMIN_* values from docker-compose.
            object.__setattr__(self, "admin_superuser_password", secrets.token_urlsafe(24))
        if not self.admin_token_secret:
            object.__setattr__(self, "admin_token_secret", secrets.token_urlsafe(48))
        return self

    model_config = SettingsConfigDict(
        env_file=str(_env_file) if _env_file.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
