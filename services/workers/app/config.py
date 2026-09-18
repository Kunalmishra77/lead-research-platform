"""Typed configuration. The only module that reads the environment (docs/12)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
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
