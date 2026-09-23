"""Typed configuration. The only module that reads the environment (docs/12)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    NODE_ENV: Literal["development", "test", "production"]
    LOG_LEVEL: Literal["debug", "info", "warning", "error"] = "info"
    DEV_DNS_OVER_HTTPS: bool = False

    DATABASE_URL_WORKERS: PostgresDsn
    DB_POOL_MAX: int = Field(default=5, ge=1, le=50)
    REDIS_URL: RedisDsn

    S3_ENDPOINT: str
    S3_REGION: str
    S3_BUCKET_RAW: str
    S3_BUCKET_EXPORTS: str
    S3_ACCESS_KEY_ID: str
    S3_SECRET_ACCESS_KEY: str

    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = None

    # Error reporting (task 1.15): off unless a DSN is set. `KEY=` in .env counts as unset.
    SENTRY_DSN_WORKERS: str | None = None
    SENTRY_ENVIRONMENT: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = Field(default=0.0, ge=0.0, le=1.0)

    # AI layer (docs/07). Without a key the gateway refuses to call a provider; nothing else
    # in the worker depends on it, so a key-less process still runs every other pool.
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str | None = None
    #: Model ids per tier. Defaults live in app/ai/config.py; set these to pin or roll back.
    AI_MODEL_SMALL: str | None = None
    AI_MODEL_MEDIUM: str | None = None
    AI_MODEL_LARGE: str | None = None
    AI_TIMEOUT_S: float = Field(default=60.0, gt=0)
    #: Turns the response cache off entirely (docs/07 caching); per-task TTLs still apply.
    AI_CACHE_ENABLED: bool = True

    #: Comma-separated pools this process consumes, e.g. "system,crawl_http" (streams jobs:<pool>).
    WORKER_POOLS: str = "system"
    #: Consumer name inside the group; defaults to host + pid at runtime.
    WORKER_NAME: str | None = None
    #: A pending message older than this is considered abandoned and reclaimed (XAUTOCLAIM).
    JOB_VISIBILITY_TIMEOUT_MS: int = Field(default=60_000, ge=1_000)
    # The envelope schema caps `attempt` at 5 (job-envelope.schema.json).
    JOB_MAX_ATTEMPTS: int = Field(default=5, ge=1, le=5)
    JOB_CONCURRENCY: int = Field(default=4, ge=1, le=256)
    JOB_RECLAIM_INTERVAL_MS: int = Field(default=5_000, ge=100)
    JOB_RETRY_BASE_DELAY_MS: int = Field(default=2_000, ge=10)

    @field_validator(
        "SENTRY_DSN_WORKERS",
        "SENTRY_ENVIRONMENT",
        "SENTRY_TRACES_SAMPLE_RATE",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "AI_MODEL_SMALL",
        "AI_MODEL_MEDIUM",
        "AI_MODEL_LARGE",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, value: object, info: ValidationInfo) -> object:
        if value != "":
            return value
        return 0.0 if info.field_name == "SENTRY_TRACES_SAMPLE_RATE" else None

    @field_validator("DATABASE_URL_WORKERS")
    @classmethod
    def _asyncpg_driver(cls, value: PostgresDsn) -> PostgresDsn:
        """SQLAlchemy needs the asyncpg dialect; accept plain postgresql:// URLs."""
        url = str(value)
        if url.startswith("postgresql://"):
            return PostgresDsn(url.replace("postgresql://", "postgresql+asyncpg://", 1))
        return value

    @property
    def pools(self) -> list[str]:
        return [p.strip() for p in self.WORKER_POOLS.split(",") if p.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
