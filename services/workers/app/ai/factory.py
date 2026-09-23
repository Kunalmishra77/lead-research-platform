"""Builds the gateway a worker process uses (docs/07).

Kept apart from the gateway itself so tests can wire a scripted provider without touching
settings, and so a handler never has to know which provider is configured.
"""

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai.gateway import AiGateway
from app.ai.providers.openai_provider import OpenAIProvider
from app.config import Settings
from app.jobs.errors import InvalidInputError
from app.metering.usage import SqlUsageRecorder


def build_gateway(
    *,
    redis: Redis,
    settings: Settings,
    engine: AsyncEngine,
    log: structlog.stdlib.BoundLogger | None = None,
) -> AiGateway:
    """Raises `InvalidInputError` when no key is configured: a pool that needs the AI layer
    should fail at startup, not on the first job that reaches a model call."""
    if not settings.OPENAI_API_KEY:
        raise InvalidInputError("OPENAI_API_KEY is not set; this worker cannot run AI tasks")
    provider = OpenAIProvider(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
    )
    return AiGateway(
        provider=provider,
        redis=redis,
        settings=settings,
        usage=SqlUsageRecorder(engine),
        log=log,
    )
