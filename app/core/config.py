from pathlib import Path
import secrets
from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


_backend_root = Path(__file__).resolve().parents[2]
_repo_root = _backend_root.parent
_env_file = _repo_root / ".env"


class Settings(BaseSettings):
    database_url: Optional[str] = Field(default=None, env="DATABASE_URL")
    postgres_user: str = Field(default="postgres", env="POSTGRES_USER")
    postgres_password: str = Field(default="", env="POSTGRES_PASSWORD")
    postgres_host: str = Field(default="localhost", env="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, env="POSTGRES_PORT")
    postgres_db: str = Field(default="wardrobe", env="POSTGRES_DB")
    cors_allowed_origins: str = Field(default="", env="CORS_ALLOWED_ORIGINS")
    service_base_url: str = Field(default="http://service:8000", env="SERVICE_BASE_URL")
    service_proxy_connect_timeout_sec: float = Field(default=10.0, env="SERVICE_PROXY_CONNECT_TIMEOUT_SEC")
    service_proxy_read_timeout_sec: float = Field(default=120.0, env="SERVICE_PROXY_READ_TIMEOUT_SEC")
    image_proxy_timeout_sec: float = Field(default=10.0, env="IMAGE_PROXY_TIMEOUT_SEC")
    image_proxy_max_bytes: int = Field(default=8_000_000, env="IMAGE_PROXY_MAX_BYTES")
    image_cache_max_age_sec: int = Field(default=86_400, env="IMAGE_CACHE_MAX_AGE_SEC")
    redis_url: str = Field(default="redis://redis:6379/0", env="REDIS_URL")
    image_cache_redis_ttl_sec: int = Field(default=259_200, ge=60, le=2_592_000, env="IMAGE_CACHE_REDIS_TTL_SEC")
    image_rate_limit_per_minute: int = Field(default=120, env="IMAGE_RATE_LIMIT_PER_MINUTE")
    weight_default_fallback_grams: int = Field(default=1000, env="DEFAULT_FALLBACK_WEIGHT_GRAMS")
    dedup_scan_limit: int = Field(default=2000, ge=10, le=100000, env="DEDUP_SCAN_LIMIT")
    dedup_score_threshold: float = Field(default=0.55, ge=0.0, le=1.0, env="DEDUP_SCORE_THRESHOLD")
    dedup_title_match_weight: float = Field(default=0.55, ge=0.0, le=1.0, env="DEDUP_TITLE_MATCH_WEIGHT")
    dedup_vendor_match_weight: float = Field(default=0.25, ge=0.0, le=1.0, env="DEDUP_VENDOR_MATCH_WEIGHT")
    dedup_price_close_weight: float = Field(default=0.15, ge=0.0, le=1.0, env="DEDUP_PRICE_CLOSE_WEIGHT")
    dedup_handle_match_weight: float = Field(default=0.2, ge=0.0, le=1.0, env="DEDUP_HANDLE_MATCH_WEIGHT")
    dedup_price_diff_ratio_limit: float = Field(default=0.08, ge=0.0, le=1.0, env="DEDUP_PRICE_DIFF_RATIO_LIMIT")
    dedup_score_cap: float = Field(default=0.99, ge=0.0, le=1.0, env="DEDUP_SCORE_CAP")
    dedup_candidates_default_limit: int = Field(default=30, ge=1, le=1000, env="DEDUP_CANDIDATES_DEFAULT_LIMIT")
    dedup_candidates_max_limit: int = Field(default=200, ge=1, le=5000, env="DEDUP_CANDIDATES_MAX_LIMIT")
    pricing_bybit_rate_auto_enabled: bool = Field(default=True, env="PRICING_BYBIT_RATE_AUTO_ENABLED")
    pricing_bybit_rate_cache_sec: int = Field(default=300, ge=30, le=86400, env="PRICING_BYBIT_RATE_CACHE_SEC")
    pricing_bybit_rate_timeout_sec: float = Field(default=12.0, ge=1.0, le=60.0, env="PRICING_BYBIT_RATE_TIMEOUT_SEC")
    pricing_bybit_fiat: str = Field(default="RUB", env="PRICING_BYBIT_FIAT")
    pricing_bybit_asset: str = Field(default="USDT", env="PRICING_BYBIT_ASSET")
    pricing_bybit_ads_limit: int = Field(default=60, ge=10, le=200, env="PRICING_BYBIT_ADS_LIMIT")
    pricing_bybit_bucket_step_usdt: int = Field(default=50, ge=10, le=1000, env="PRICING_BYBIT_BUCKET_STEP_USDT")
    pricing_bybit_bucket_max_usdt: int = Field(default=5000, ge=50, le=20000, env="PRICING_BYBIT_BUCKET_MAX_USDT")
    pricing_bybit_outlier_max_deviation_ratio: float = Field(
        default=0.08,
        ge=0.0,
        le=0.5,
        env="PRICING_BYBIT_OUTLIER_MAX_DEVIATION_RATIO",
    )
    pricing_bybit_worker_interval_sec: int = Field(
        default=10800,
        ge=30,
        le=86400,
        env="PRICING_BYBIT_WORKER_INTERVAL_SEC",
    )
    admin_superuser_login: str = Field(default="admin", env="ADMIN_SUPERUSER_LOGIN")
    admin_superuser_password: str = Field(default="", env="ADMIN_SUPERUSER_PASSWORD")
    admin_access_token_ttl_sec: int = Field(default=86_400, ge=300, le=2_592_000, env="ADMIN_ACCESS_TOKEN_TTL_SEC")
    admin_refresh_token_ttl_sec: int = Field(default=604_800, ge=3600, le=7_776_000, env="ADMIN_REFRESH_TOKEN_TTL_SEC")
    admin_token_secret: str = Field(default="", env="ADMIN_TOKEN_SECRET")

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

    class Config:
        env_file = str(_env_file) if _env_file.exists() else None
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
