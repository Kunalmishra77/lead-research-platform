"""Async SQLAlchemy Core engine (asyncpg), role app_worker via the Supabase transaction pooler."""

from uuid import uuid4

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    # SQLAlchemy's asyncpg dialect keeps its own statement cache besides asyncpg's; disable both.
    url = make_url(str(settings.DATABASE_URL_WORKERS)).update_query_dict(
        {"prepared_statement_cache_size": "0"}
    )
    return create_async_engine(
        url,
        pool_size=settings.DB_POOL_MAX,
        max_overflow=0,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={
            # Transaction pooling: no cached statements, and unique names if any are prepared.
            "statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__lf_{uuid4().hex}__",
            "server_settings": {"application_name": "leadforge-workers"},
            "timeout": 10,
        },
    )
