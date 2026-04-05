from pathlib import Path
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
    app_title: str = Field(default="Wardrobe Parser Backend API", env="APP_TITLE")
    health_status_value: str = Field(default="ok", env="HEALTH_STATUS_VALUE")
    cors_allow_credentials: bool = Field(default=True, env="CORS_ALLOW_CREDENTIALS")
    cors_allow_methods: str = Field(default="*", env="CORS_ALLOW_METHODS")
    cors_allow_headers: str = Field(default="*", env="CORS_ALLOW_HEADERS")
    service_base_url: str = Field(default="http://service:8000", env="SERVICE_BASE_URL")
    service_proxy_connect_timeout_sec: float = Field(default=10.0, env="SERVICE_PROXY_CONNECT_TIMEOUT_SEC")
    service_proxy_read_timeout_sec: float = Field(default=120.0, env="SERVICE_PROXY_READ_TIMEOUT_SEC")
    image_proxy_timeout_sec: float = Field(default=10.0, env="IMAGE_PROXY_TIMEOUT_SEC")
    image_proxy_max_bytes: int = Field(default=8_000_000, env="IMAGE_PROXY_MAX_BYTES")
    image_cache_max_age_sec: int = Field(default=86_400, env="IMAGE_CACHE_MAX_AGE_SEC")
    image_rate_limit_per_minute: int = Field(default=120, env="IMAGE_RATE_LIMIT_PER_MINUTE")
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

    @model_validator(mode="after")
    def build_database_url(self) -> "Settings":
        if not self.database_url:
            url = (
                f"postgresql://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
            object.__setattr__(self, "database_url", url)
        return self

    class Config:
        env_file = str(_env_file) if _env_file.exists() else None
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
