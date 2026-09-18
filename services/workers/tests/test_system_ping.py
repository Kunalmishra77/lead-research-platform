"""system.ping handler through the real consumer (fakeredis + in-memory job_runs repo)."""

import asyncio
import json
from typing import Any

from redis.asyncio import Redis

from app.handlers import build_registry
from app.jobs.publisher import publish
from app.jobs.retry import delayed_key, promote_due
from tests.conftest import STREAM, ConsumerFactory, EnvelopeFactory

DLQ = f"dlq:{STREAM}"


class FakeJobRuns:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.finished: set[str] = set()

    async def mark_running(self, org_id: str, job_id: str, attempt: int) -> bool:
        if job_id in self.finished:
            return False
        self.calls.append(("running", job_id, str(attempt)))
        return True

    async def mark_completed(self, org_id: str, job_id: str, result: dict[str, Any]) -> bool:
        self.calls.append(("completed", job_id, json.dumps(result, sort_keys=True)))
        self.finished.add(job_id)
        return True

    async def mark_failed(
        self, org_id: str, job_id: str, error_class: str, error: str, attempts: int | None
    ) -> bool:
        self.calls.append(("failed", job_id, error_class, str(attempts)))
        self.finished.add(job_id)
        return True


async def _force_due(redis: Redis) -> None:
    key = delayed_key(STREAM)
    for member in await redis.zrange(key, 0, -1):
        await redis.zadd(key, {member: 0})
    await promote_due(redis, STREAM)


async def _events(pubsub: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for _ in range(40):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.02)
        if message is None:
            if events:
                break
            await asyncio.sleep(0.01)
            continue
        events.append(json.loads(message["data"]))
    return events


async def test_ping_completes_with_progress_and_db_updates(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    repo = FakeJobRuns()
    consumer = make_consumer(build_registry(job_runs=repo))
    await consumer.ensure_group()
    envelope = make_envelope("system.ping", payload={"message": "hi", "steps": 2})
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"progress:{envelope.job_id}")
    await publish(redis, STREAM, envelope)

    await consumer.run_once()
    job = str(envelope.job_id)
    assert repo.calls == [
        ("running", job, "1"),
        ("completed", job, json.dumps({"attempts": 1, "echo": "hi"}, sort_keys=True)),
    ]
    events = await _events(pubsub)
    assert [(e["status"], e["counts"]["step"]) for e in events] == [
        ("running", 0),
        ("running", 1),
        ("running", 2),
        ("completed", 2),
    ]
    assert all(e["trace_id"] == envelope.trace_id and e["stage"] == "ping" for e in events)
    await pubsub.aclose()


async def test_ping_succeeds_after_transient_failures(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    repo = FakeJobRuns()
    consumer = make_consumer(build_registry(job_runs=repo))
    await consumer.ensure_group()
    await publish(redis, STREAM, make_envelope("system.ping", payload={"fail_times": 2}))

    for _ in range(3):
        await consumer.run_once()
        await _force_due(redis)
    assert [c[0] for c in repo.calls] == ["running", "running", "running", "completed"]
    assert await redis.xlen(DLQ) == 0


async def test_ping_failing_five_times_is_dead_lettered_and_marked_failed(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    repo = FakeJobRuns()
    consumer = make_consumer(build_registry(job_runs=repo))
    await consumer.ensure_group()
    envelope = make_envelope("system.ping", payload={"fail_times": 5})
    await publish(redis, STREAM, envelope)

    for _ in range(5):
        await consumer.run_once()
        await _force_due(redis)
    assert repo.calls[-1] == ("failed", str(envelope.job_id), "transient", "5")
    assert await redis.xlen(DLQ) == 1


async def test_ping_rejects_bad_payload_and_missing_org(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    repo = FakeJobRuns()
    consumer = make_consumer(build_registry(job_runs=repo))
    await consumer.ensure_group()
    bad = make_envelope("system.ping", payload={"delay_ms": -1, "unexpected": True})
    orgless = make_envelope("system.ping", org_id=None)
    await publish(redis, STREAM, bad)
    await publish(redis, STREAM, orgless)

    await consumer.run_once()
    entries = await redis.xrange(DLQ)
    assert [e[1][b"error_class"] for e in entries] == [b"invalid_input", b"invalid_input"]
    # Only the job with an org can have its row marked failed.
    assert repo.calls == [("failed", str(bad.job_id), "invalid_input", "1")]


async def test_redelivery_of_a_finished_job_does_not_run_it_again(
    redis: Redis, make_consumer: ConsumerFactory, make_envelope: EnvelopeFactory
) -> None:
    repo = FakeJobRuns()
    consumer = make_consumer(build_registry(job_runs=repo))
    await consumer.ensure_group()
    envelope = make_envelope("system.ping")
    repo.finished.add(str(envelope.job_id))  # e.g. completed earlier, idempotency key expired
    await publish(redis, STREAM, envelope)

    await consumer.run_once()
    assert repo.calls == []
    assert await redis.xlen(DLQ) == 0
