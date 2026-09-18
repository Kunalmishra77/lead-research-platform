"""Job framework behaviour on fakeredis (no live services)."""

import asyncio
import json

from redis.asyncio import Redis

from app.jobs.context import JobContext
from app.jobs.errors import (
    AccessRestrictedError,
    BudgetExhaustedError,
    RateLimitedError,
    TransientError,
)
from app.jobs.idempotency import IdempotencyGuard, idempotency_key
from app.jobs.publisher import publish
from app.jobs.registry import HandlerRegistry
from app.jobs.retry import backoff_seconds, delayed_key, promote_due
from tests.conftest import GROUP, STREAM, ConsumerFactory, EnvelopeFactory

DLQ = f"dlq:{STREAM}"
DELAYED = delayed_key(STREAM)


async def _force_due(redis: Redis) -> int:
    """Makes every delayed retry due now and promotes it (instead of sleeping through backoff)."""
    members = await redis.zrange(DELAYED, 0, -1)
    for member in members:
        await redis.zadd(DELAYED, {member: 0})
    return await promote_due(redis, STREAM)


async def _pending(redis: Redis) -> int:
    summary = await redis.xpending(STREAM, GROUP)
    return int(summary["pending"])


def _registry(handler_errors: list[Exception | None], calls: list[JobContext]) -> HandlerRegistry:
    """A test.echo handler that raises the next queued error (None = succeed)."""
    registry = HandlerRegistry()

    @registry.register("test.echo", stage="ping")
    async def echo(ctx: JobContext) -> None:
        calls.append(ctx)
        error = handler_errors.pop(0) if handler_errors else None
        if error is not None:
            raise error

    return registry


async def test_success_runs_handler_once_and_acks(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    calls: list[JobContext] = []
    consumer = make_consumer(_registry([], calls))
    await consumer.ensure_group()
    envelope = make_envelope()
    await publish(redis, STREAM, envelope)

    assert await consumer.run_once() == 1
    assert [c.job_id for c in calls] == [str(envelope.job_id)]
    assert await _pending(redis) == 0
    key = idempotency_key(str(envelope.org_id), envelope.idempotency_key)
    assert await redis.get(key) == b"done"


async def test_duplicate_delivery_is_acknowledged_without_rerunning(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    calls: list[JobContext] = []
    consumer = make_consumer(_registry([], calls))
    await consumer.ensure_group()
    envelope = make_envelope()
    await publish(redis, STREAM, envelope)
    await publish(redis, STREAM, envelope)

    await consumer.run_once()
    await consumer.run_once()
    assert len(calls) == 1
    assert await _pending(redis) == 0


async def test_transient_failure_is_retried_with_next_attempt(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    calls: list[JobContext] = []
    consumer = make_consumer(_registry([TransientError("flaky")], calls))
    await consumer.ensure_group()
    await publish(redis, STREAM, make_envelope())

    await consumer.run_once()
    assert await _pending(redis) == 0  # original acked
    assert await redis.zcard(DELAYED) == 1
    assert await _force_due(redis) == 1

    await consumer.run_once()
    assert [c.envelope.attempt for c in calls] == [1, 2]
    assert await redis.xlen(DLQ) == 0


async def test_job_failing_five_times_lands_in_dlq(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    calls: list[JobContext] = []
    errors: list[Exception | None] = [TransientError(f"boom {i}") for i in range(5)]
    consumer = make_consumer(_registry(errors, calls))
    await consumer.ensure_group()
    await publish(redis, STREAM, make_envelope())

    for _ in range(5):
        await consumer.run_once()
        await _force_due(redis)

    assert [c.envelope.attempt for c in calls] == [1, 2, 3, 4, 5]
    entries = await redis.xrange(DLQ)
    assert len(entries) == 1
    fields = entries[0][1]
    assert fields[b"error_class"] == b"transient"
    assert fields[b"attempts"] == b"5"
    assert json.loads(fields[b"envelope"])["attempt"] == 5
    assert await redis.zcard(DELAYED) == 0
    assert await _pending(redis) == 0


async def test_access_restricted_is_never_retried(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    calls: list[JobContext] = []
    consumer = make_consumer(_registry([AccessRestrictedError("robots disallow")], calls))
    await consumer.ensure_group()
    await publish(redis, STREAM, make_envelope())

    await consumer.run_once()
    assert len(calls) == 1
    assert await redis.zcard(DELAYED) == 0
    [(_, fields)] = await redis.xrange(DLQ)
    assert fields[b"error_class"] == b"access_restricted"


async def test_budget_exhausted_pauses_without_dlq(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    consumer = make_consumer(_registry([BudgetExhaustedError("cap reached")], []))
    await consumer.ensure_group()
    envelope = make_envelope()
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"progress:{envelope.job_id}")
    await publish(redis, STREAM, envelope)

    await consumer.run_once()
    assert await redis.xlen(DLQ) == 0
    assert await _pending(redis) == 0
    event = await _next_message(pubsub)
    assert event["status"] == "paused"
    assert event["error_class"] == "budget_exhausted"
    assert event["stage"] == "ping"
    await pubsub.aclose()


async def test_failed_job_publishes_failed_progress_with_trace(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    consumer = make_consumer(_registry([AccessRestrictedError("login wall")], []))
    await consumer.ensure_group()
    envelope = make_envelope()
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"progress:{envelope.job_id}")
    await publish(redis, STREAM, envelope)

    await consumer.run_once()
    event = await _next_message(pubsub)
    assert event["status"] == "failed"
    assert event["trace_id"] == envelope.trace_id
    assert event["org_id"] == str(envelope.org_id)
    await pubsub.aclose()


async def test_invalid_envelopes_go_straight_to_dlq(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    calls: list[JobContext] = []
    consumer = make_consumer(_registry([], calls))
    await consumer.ensure_group()
    await redis.xadd(STREAM, {"envelope": "{not json"})
    orphan = make_envelope(research_job_id="01923f4e-7b3a-7c2d-9f10-00000000abcd", org_id=None)
    await publish(redis, STREAM, orphan)  # tenant rule: research job without org
    await publish(redis, STREAM, make_envelope(job_type="nobody.handles"))
    await redis.xadd(STREAM, {"other": "field"})

    await consumer.run_once()
    entries = await redis.xrange(DLQ)
    assert [e[1][b"error_class"] for e in entries] == [b"invalid_input"] * 4
    assert calls == []
    assert await _pending(redis) == 0


async def test_stale_message_of_a_dead_consumer_is_reclaimed(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    calls: list[JobContext] = []
    survivor = make_consumer(_registry([], calls), name="survivor", visibility_timeout_ms=0)
    await survivor.ensure_group()
    await publish(redis, STREAM, make_envelope())
    # A consumer reads the message and "crashes" before acking.
    await redis.xreadgroup(GROUP, "crashed", {STREAM: ">"}, count=1)
    assert await _pending(redis) == 1

    assert await survivor.reclaim_stale() == 1
    assert len(calls) == 1
    assert await _pending(redis) == 0


async def test_crash_looping_message_is_dead_lettered_on_reclaim(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    calls: list[JobContext] = []
    survivor = make_consumer(_registry([], calls), name="survivor", visibility_timeout_ms=0)
    await survivor.ensure_group()
    await publish(redis, STREAM, make_envelope())
    await redis.xreadgroup(GROUP, "crashed", {STREAM: ">"}, count=1)
    for i in range(5):  # five more crashed deliveries
        await redis.xautoclaim(STREAM, GROUP, f"crashed-{i}", min_idle_time=0, start_id="0-0")

    await survivor.reclaim_stale()
    assert calls == []
    [(_, fields)] = await redis.xrange(DLQ)
    assert b"delivered" in fields[b"error"]


async def test_rate_limited_backoff_respects_retry_after() -> None:
    assert backoff_seconds(1, retry_after_s=90) >= 90
    assert backoff_seconds(10) <= 300
    error = RateLimitedError("429", retry_after_s=30)
    assert error.retry_after_s == 30


async def _next_message(pubsub: object) -> dict[str, object]:
    for _ in range(50):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05)  # type: ignore[attr-defined]
        if message is not None:
            data: dict[str, object] = json.loads(message["data"])
            return data
        await asyncio.sleep(0.01)
    raise AssertionError("no progress event published")


async def test_crash_after_claiming_is_taken_over_by_the_reclaimer(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    calls: list[JobContext] = []
    survivor = make_consumer(_registry([], calls), name="survivor", visibility_timeout_ms=0)
    await survivor.ensure_group()
    envelope = make_envelope()
    await publish(redis, STREAM, envelope)
    # The crashed worker read the message AND took the idempotency claim before dying.
    [(_, [(message_id, _fields)])] = await redis.xreadgroup(
        GROUP, "crashed", {STREAM: ">"}, count=1
    )
    key = idempotency_key(str(envelope.org_id), envelope.idempotency_key)
    assert await IdempotencyGuard(redis, 900).claim(key, message_id.decode()) == "acquired"

    assert await survivor.reclaim_stale() == 1
    assert len(calls) == 1
    assert await _pending(redis) == 0
    assert await redis.get(key) == b"done"
    assert await redis.xlen(DLQ) == 0


async def test_concurrent_duplicate_messages_run_the_job_once(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    calls: list[JobContext] = []
    consumer = make_consumer(_registry([], calls))
    await consumer.ensure_group()
    envelope = make_envelope()
    await publish(redis, STREAM, envelope)
    await publish(redis, STREAM, envelope)

    assert await consumer.run_once() == 2  # both in one batch, processed concurrently
    assert len(calls) == 1
    assert await _pending(redis) == 0


async def test_heartbeat_keeps_a_long_job_from_being_reclaimed(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    registry = HandlerRegistry()
    calls: list[str] = []

    @registry.register("test.slow", stage="ping")
    async def slow(ctx: JobContext) -> None:
        calls.append(ctx.job_id)
        await asyncio.sleep(0.4)

    worker = make_consumer(registry, name="worker", visibility_timeout_ms=100)
    rival = make_consumer(registry, name="rival", visibility_timeout_ms=100)
    await worker.ensure_group()
    await publish(redis, STREAM, make_envelope(job_type="test.slow"))

    async def rival_keeps_reclaiming() -> int:
        reclaimed = 0
        for _ in range(8):
            await asyncio.sleep(0.05)
            reclaimed += await rival.reclaim_stale()
        return reclaimed

    _, reclaimed = await asyncio.gather(worker.run_once(), rival_keeps_reclaiming())
    assert reclaimed == 0
    assert len(calls) == 1
    assert await _pending(redis) == 0
    assert await redis.xlen(DLQ) == 0
