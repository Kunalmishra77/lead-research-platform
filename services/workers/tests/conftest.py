import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import fakeredis
import pytest
import structlog
from leadforge_contracts.job_envelope import JobEnvelope
from redis.asyncio import Redis

from app.jobs.consumer import ConsumerSettings, StreamConsumer
from app.jobs.registry import HandlerRegistry

os.environ.setdefault("NODE_ENV", "test")

STREAM = "jobs:test"
GROUP = "workers:test"


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    """fakeredis by default. REDIS_TEST_URL runs the same suite against a real Redis; point it at
    a dedicated database (e.g. redis://127.0.0.1:6379/15): it is flushed before every test."""
    url = os.environ.get("REDIS_TEST_URL")
    client: Redis = Redis.from_url(url) if url else fakeredis.FakeAsyncRedis()
    if url:
        await client.flushdb()
    yield client
    await client.aclose()


EnvelopeFactory = Callable[..., JobEnvelope]


@pytest.fixture
def make_envelope() -> EnvelopeFactory:
    def factory(job_type: str = "test.echo", **overrides: Any) -> JobEnvelope:
        job_id = str(uuid4())
        data: dict[str, Any] = {
            "envelope_version": 1,
            "job_id": job_id,
            "type": job_type,
            "org_id": str(uuid4()),
            "research_job_id": None,
            "idempotency_key": f"test:{job_id}",
            "attempt": 1,
            "priority": "interactive",
            "budget": {"credits_remaining": 0, "cost_cap_micros": 0},
            "trace_id": uuid4().hex,
            "payload": {},
            "created_at": datetime.now(UTC).isoformat(),
        }
        data.update(overrides)
        return JobEnvelope.model_validate(data)

    return factory


ConsumerFactory = Callable[..., StreamConsumer]


@pytest.fixture
def make_consumer(redis: Redis) -> ConsumerFactory:
    def factory(
        registry: HandlerRegistry, name: str = "c1", visibility_timeout_ms: int = 60_000
    ) -> StreamConsumer:
        return StreamConsumer(
            redis,
            registry,
            ConsumerSettings(
                stream=STREAM,
                group=GROUP,
                consumer=name,
                max_attempts=5,
                visibility_timeout_ms=visibility_timeout_ms,
                batch_size=10,
                block_ms=10,
            ),
            structlog.get_logger("test"),
        )

    return factory
