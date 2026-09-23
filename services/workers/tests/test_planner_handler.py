"""The `research.plan` handler (task 2.10): what it spends, and when it stops.

The plan itself is covered in test_planner.py. What matters here is everything around it — the
cancel flag (ADR-0008), the budget each fanned-out envelope carries, and what happens when the
model is unavailable — because each of those decides whether a user is billed for work.
"""

import json
from typing import Any

import pytest
import structlog
from redis.asyncio import Redis

from app.db.research_tasks import SavedTask
from app.handlers.planner import (
    DEFAULT_DISCOVERY_POOL,
    MAX_QUERIES,
    register_planner_handlers,
)
from app.jobs.cancellation import CANCEL_KEY
from app.jobs.context import JobContext
from app.jobs.envelope import ENVELOPE_FIELD, stream_for
from app.jobs.errors import InvalidInputError
from app.jobs.progress import ProgressPublisher
from app.jobs.registry import HandlerRegistry
from app.planner.plan import PlannedTask
from tests.planner_support import FakeReference, make_registry, make_spec

ORG = "018f4a9a-0000-7000-8000-000000000001"
JOB = "018f4a9a-0000-7000-8000-000000000002"


class FakeGateway:
    """Stands in for the AI gateway: returns one expansion, or raises."""

    def __init__(self, data: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self._data = data or {"queries": ["dental clinics in Pune"], "sub_localities": []}
        self._error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run(self, task: str, payload: dict[str, Any], ctx: Any, **kwargs: Any) -> Any:
        self.calls.append((task, payload))
        if self._error is not None:
            raise self._error

        class Result:
            data = self._data

        return Result()


class FakeJobsRepo:
    """Records the run-state the planner would have written to app.research_jobs."""

    def __init__(self) -> None:
        self.failed: list[tuple[str, str]] = []
        self.cancelled: list[str] = []

    async def mark_failed(self, org_id: str, job_id: str, error_class: str, message: str) -> bool:
        self.failed.append((error_class, message))
        return True

    async def mark_cancelled(self, org_id: str, job_id: str) -> bool:
        self.cancelled.append(job_id)
        return True


class FakeTasksRepo:
    """Records what a plan would have written, and hands back ids like the real one."""

    def __init__(self) -> None:
        self.saved: list[list[PlannedTask]] = []

    async def save_plan(
        self, org_id: str, job_id: str, tasks: list[PlannedTask]
    ) -> list[SavedTask]:
        self.saved.append(list(tasks))
        return [
            SavedTask(f"018f4a9a-0000-7000-8000-00000000{i:04d}", task, inserted=True)
            for i, task in enumerate(tasks)
        ]


def build(
    gateway: Any,
    repo: FakeTasksRepo,
    jobs: FakeJobsRepo,
    *,
    places: bool = True,
    pool: str = DEFAULT_DISCOVERY_POOL,
) -> HandlerRegistry:
    registry = HandlerRegistry()
    register_planner_handlers(
        registry,
        gateway=gateway,
        connectors=make_registry(places=places),
        reference=FakeReference(),
        tasks=repo,
        jobs=jobs,
        pool=pool,
    )
    return registry


async def run_plan(
    redis: Redis,
    make_envelope: Any,
    *,
    gateway: Any,
    repo: FakeTasksRepo,
    spec: Any = None,
    payload: dict[str, Any] | None = None,
    budget: dict[str, int] | None = None,
    jobs: FakeJobsRepo | None = None,
    places: bool = True,
    pool: str = DEFAULT_DISCOVERY_POOL,
) -> None:
    registry = build(gateway, repo, jobs or FakeJobsRepo(), places=places, pool=pool)
    envelope = make_envelope(
        "research.plan",
        job_id=JOB,
        org_id=ORG,
        research_job_id=JOB,
        budget=budget or {"credits_remaining": 1000, "cost_cap_micros": 20_000_000},
        payload=payload
        if payload is not None
        else {
            "search_id": "s-1",
            "spec": json.loads((spec or make_spec(cities=["Pune"])).model_dump_json()),
        },
    )
    handler = registry.get("research.plan")
    assert handler is not None
    await handler(
        JobContext(
            envelope=envelope,
            redis=redis,
            progress=ProgressPublisher(redis),
            log=structlog.get_logger("test"),
            message_id="1-0",
        )
    )


async def drain(redis: Redis, pool: str = DEFAULT_DISCOVERY_POOL) -> list[dict[str, Any]]:
    entries = await redis.xrange(stream_for(pool))
    out = []
    for _, fields in entries:
        raw = (
            fields[ENVELOPE_FIELD.encode()]
            if isinstance(next(iter(fields)), bytes)
            else fields[ENVELOPE_FIELD]
        )
        out.append(json.loads(raw))
    return out


async def test_a_plan_queues_one_envelope_per_task(redis: Redis, make_envelope: Any) -> None:
    repo = FakeTasksRepo()
    await run_plan(redis, make_envelope, gateway=FakeGateway(), repo=repo)

    queued = await drain(redis)
    assert len(queued) == len(repo.saved[0]) == 1
    assert queued[0]["type"] == "discovery.places_text_search"
    assert queued[0]["research_job_id"] == JOB
    assert queued[0]["org_id"] == ORG


async def test_each_task_is_given_its_own_budget_not_the_whole_job(
    redis: Redis, make_envelope: Any
) -> None:
    repo = FakeTasksRepo()
    gateway = FakeGateway({"queries": ["a", "b", "c", "d"], "sub_localities": []})
    await run_plan(
        redis,
        make_envelope,
        gateway=gateway,
        repo=repo,
        spec=make_spec(cities=["Pune"], max_credits=1000, max_results=500),
        # What estimateFor actually reserves for that spec at 1 credit/lead: half the ceiling.
        budget={"credits_remaining": 500, "cost_cap_micros": 10_000_000},
    )

    queued = await drain(redis)
    assert len(queued) == 4
    caps = [q["budget"]["cost_cap_micros"] for q in queued]
    credits = [q["budget"]["credits_remaining"] for q in queued]
    # cost_cap_micros is a total the callee must not decrement (metering/context.py). Handing
    # each task the job's whole cap would let any one of them spend all of it.
    assert sum(credits) == sum(t.credit_budget for t in repo.saved[0])
    assert max(credits) < 500
    # And the whole plan stays inside what was reserved, not inside what the user said they
    # would tolerate. Splitting max_credits here would have handed out 600 of 500.
    assert sum(credits) <= 500
    # Priced at the job's own rate rather than a constant copied from the API, so a change to
    # COST_CAP_MICROS_PER_CREDIT cannot leave the worker quietly disagreeing about the ceiling.
    assert caps == [c * 20_000 for c in credits]
    assert sum(caps) <= 10_000_000


async def test_an_uncapped_job_does_not_become_a_thousand_capped_tasks(
    redis: Redis, make_envelope: Any
) -> None:
    repo = FakeTasksRepo()
    await run_plan(
        redis,
        make_envelope,
        gateway=FakeGateway(),
        repo=repo,
        # Credits to spend, but no internal micro cap: zero means uncapped system-wide.
        budget={"credits_remaining": 500, "cost_cap_micros": 0},
    )

    # Turning that into a positive per-task cap would invent a ceiling nobody set, and
    # connectors refuse a call that cannot fit one.
    queued = await drain(redis)
    assert queued != []
    assert all(q["budget"]["cost_cap_micros"] == 0 for q in queued)


async def test_each_task_carries_its_own_idempotency_key(redis: Redis, make_envelope: Any) -> None:
    repo = FakeTasksRepo()
    gateway = FakeGateway({"queries": ["a", "b"], "sub_localities": []})
    await run_plan(redis, make_envelope, gateway=gateway, repo=repo)

    keys = [q["idempotency_key"] for q in await drain(redis)]
    # Built from the task's row id, so a redelivered plan fans out the same keys and the
    # executor's guard recognises the repeat rather than running everything twice.
    assert len(set(keys)) == len(keys)
    assert all(k.startswith("discovery.places_text_search:") for k in keys)


async def test_a_job_with_nothing_to_plan_is_ended_rather_than_left_hanging(
    redis: Redis, make_envelope: Any
) -> None:
    repo, jobs = FakeTasksRepo(), FakeJobsRepo()
    # Only serp is registered, and serp cannot find businesses.
    await run_plan(redis, make_envelope, gateway=FakeGateway(), repo=repo, jobs=jobs, places=False)

    # Nothing downstream will ever finish this job, so left at `planning` it sits for ever with
    # the user's credits reserved and no visible failure -- a silent failure (CLAUDE.md).
    assert await drain(redis) == []
    assert len(jobs.failed) == 1
    error_class, message = jobs.failed[0]
    assert error_class == "invalid_input"
    assert "discover" in message


async def test_a_budget_too_small_to_search_says_so_in_the_right_words(
    redis: Redis, make_envelope: Any
) -> None:
    repo, jobs = FakeTasksRepo(), FakeJobsRepo()
    await run_plan(
        redis,
        make_envelope,
        gateway=FakeGateway(),
        repo=repo,
        jobs=jobs,
        budget={"credits_remaining": 0, "cost_cap_micros": 0},
    )

    # "Your request cannot be served" and "your budget cannot" are different problems with
    # different fixes, and the error taxonomy is how the UI tells them apart (docs/01).
    assert jobs.failed[0][0] == "budget_exhausted"


async def test_the_fan_out_uses_the_pool_it_was_configured_with(
    redis: Redis, make_envelope: Any
) -> None:
    repo = FakeTasksRepo()
    await run_plan(redis, make_envelope, gateway=FakeGateway(), repo=repo, pool="discovery_test")

    # The API publishes to JOBS_DISCOVERY_POOL, which is configurable and which its own tests
    # override per run. Hardcoding "discovery" would put these on a stream nobody consumes.
    assert await drain(redis, "discovery_test") != []
    assert await drain(redis, DEFAULT_DISCOVERY_POOL) == []


async def test_a_worker_with_no_model_key_can_still_plan(redis: Redis, make_envelope: Any) -> None:
    repo = FakeTasksRepo()
    await run_plan(redis, make_envelope, gateway=None, repo=repo)

    # Expansion is the only model call this stage makes and it already degrades to the request
    # as written. Refusing to register would leave research.plan with no handler and the job
    # hanging, for a stage that does not need a model.
    queued = await drain(redis)
    assert len(queued) == 1
    assert "dental clinics" in queued[0]["payload"]["query"]["text"]


async def test_a_cancelled_job_is_not_planned_at_all(redis: Redis, make_envelope: Any) -> None:
    await redis.set(CANCEL_KEY.format(job_id=JOB), "1")
    repo = FakeTasksRepo()
    gateway = FakeGateway()

    await run_plan(redis, make_envelope, gateway=gateway, repo=repo)

    # The envelope may have sat in the stream while the user changed their mind. The API has
    # already released the credits, so anything spent now is unmetered (ADR-0008).
    assert repo.saved == []
    assert await drain(redis) == []
    assert gateway.calls == []


async def test_a_job_cancelled_while_planning_writes_no_tasks_at_all(
    redis: Redis, make_envelope: Any
) -> None:
    repo, jobs = FakeTasksRepo(), FakeJobsRepo()

    class CancellingGateway(FakeGateway):
        async def run(self, task: str, payload: dict[str, Any], ctx: Any, **kw: Any) -> Any:
            await redis.set(CANCEL_KEY.format(job_id=JOB), "1")
            return await super().run(task, payload, ctx, **kw)

    await run_plan(redis, make_envelope, gateway=CancellingGateway(), repo=repo, jobs=jobs)

    # The check sits before the write, not after it. Writing the plan first left a cancelled job
    # holding a set of tasks stuck at `queued` for ever, which ADR-0008 explicitly rules out.
    assert repo.saved == []
    assert await drain(redis) == []
    assert jobs.cancelled == [JOB]


async def test_an_unavailable_model_plans_the_request_as_written(
    redis: Redis, make_envelope: Any
) -> None:
    repo = FakeTasksRepo()
    gateway = FakeGateway(error=RuntimeError("provider down"))

    await run_plan(redis, make_envelope, gateway=gateway, repo=repo)

    # One plain search is a worse plan, not a broken one. Failing here would cost the user the
    # whole job because a synonym list could not be fetched.
    queued = await drain(redis)
    assert len(queued) == 1
    assert "dental clinics" in queued[0]["payload"]["query"]["text"]


async def test_an_over_generous_expansion_cannot_become_an_unbounded_bill(
    redis: Redis, make_envelope: Any
) -> None:
    repo = FakeTasksRepo()
    gateway = FakeGateway({"queries": [f"q{i}" for i in range(50)], "sub_localities": []})

    await run_plan(redis, make_envelope, gateway=gateway, repo=repo)

    # The output schema deliberately sets no array bounds (strict structured output is only
    # verified to accept minimum/maximum), so the clamp has to live here. 50 phrases would
    # otherwise be 50 paid searches.
    assert len(repo.saved[0]) <= MAX_QUERIES


async def test_a_duplicate_phrase_is_not_searched_twice(redis: Redis, make_envelope: Any) -> None:
    repo = FakeTasksRepo()
    gateway = FakeGateway({"queries": ["dentists", "dentists", "clinics"], "sub_localities": []})

    await run_plan(redis, make_envelope, gateway=gateway, repo=repo)

    texts = [q["payload"]["query"]["text"] for q in await drain(redis)]
    assert texts == ["dentists", "clinics"]


async def test_an_expansion_failure_is_classified_and_redacted(
    redis: Redis, make_envelope: Any
) -> None:
    events: list[dict[str, Any]] = []

    class Recorder(FakeGateway):
        async def run(self, task: str, payload: dict[str, Any], ctx: Any, **kw: Any) -> Any:
            raise RuntimeError("boom https://api.example.test/v1?key=sk-secret-value")

    repo = FakeTasksRepo()
    registry = build(Recorder(), repo, FakeJobsRepo())
    envelope = make_envelope(
        "research.plan",
        job_id=JOB,
        org_id=ORG,
        research_job_id=JOB,
        budget={"credits_remaining": 500, "cost_cap_micros": 10_000_000},
        payload={
            "search_id": "s-1",
            "spec": json.loads(make_spec(cities=["Pune"]).model_dump_json()),
        },
    )

    class Log:
        def warning(self, event: str, **kw: Any) -> None:
            events.append({"event": event, **kw})

        def info(self, event: str, **kw: Any) -> None: ...

    handler = registry.get("research.plan")
    assert handler is not None
    await handler(
        JobContext(
            envelope=envelope,
            redis=redis,
            progress=ProgressPublisher(redis),
            log=Log(),  # type: ignore[arg-type]
            message_id="1-0",
        )
    )

    warned = next(e for e in events if "expansion unavailable" in e["event"])
    # app/jobs/redact.py exists because an HTTP client's exception carries the URL, and the URL
    # carries the API key. Logging the raw exception is how a key reaches the log aggregator.
    assert "sk-secret-value" not in str(warned)
    assert warned["error_class"] == "transient"


async def test_a_spec_that_does_not_validate_fails_the_job_rather_than_retrying(
    redis: Redis, make_envelope: Any
) -> None:
    repo = FakeTasksRepo()
    with pytest.raises(InvalidInputError):
        await run_plan(
            redis,
            make_envelope,
            gateway=FakeGateway(),
            repo=repo,
            payload={"search_id": "s-1", "spec": {"spec_version": 1}},
        )
    assert repo.saved == []


async def test_the_expander_is_asked_about_the_request_not_the_whole_spec(
    redis: Redis, make_envelope: Any
) -> None:
    gateway = FakeGateway()
    await run_plan(redis, make_envelope, gateway=gateway, repo=FakeTasksRepo())

    task, payload = gateway.calls[0]
    assert task == "query_expand"
    # The place must reach the model as its name, not as the contract's RootModel repr: asking
    # it to expand "root='Pune'" would return areas of nowhere.
    assert payload["query"] == "dental clinics in Pune"
    assert payload["country"] == "IN"
