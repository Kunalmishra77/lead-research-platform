"""The discovery executor (task 2.11): what it charges for, and when it refuses to run.

This is the first handler that turns money into leads, so what is pinned here is the billing and
the stopping: a job pays for businesses it did not already have, a redelivered task does not buy
the same search twice, and a cancelled job buys nothing at all.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
import structlog
from redis.asyncio import Redis

from app.connectors.types import Candidate, FieldValue
from app.db.graph import Stored
from app.handlers.discovery import register_discovery_handlers
from app.jobs.cancellation import CANCEL_KEY
from app.jobs.context import JobContext
from app.jobs.errors import InvalidInputError, TransientError
from app.jobs.progress import ProgressPublisher
from app.jobs.registry import HandlerRegistry

ORG = "018f4a9a-0000-7000-8000-000000000001"
JOB = "018f4a9a-0000-7000-8000-000000000002"
TASK = "018f4a9a-0000-7000-8000-000000000003"
WORKSPACE = "018f4a9a-0000-7000-8000-000000000004"
TASK_TYPE = "discovery.places_text_search"

PAYLOAD = {
    "source": "google_places",
    "query": {
        "text": "dental clinics in Pune",
        "bbox": [18.4, 73.7, 18.6, 74.0],
        "country": "IN",
        "language": "en",
        "max_results": 100,
    },
    "area_slug": "pune-maharashtra",
    "depth": "standard",
}


def candidate(place_id: str, name: str) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(
        source_key="google_places",
        external_id=place_id,
        name=name,
        source_url=f"https://maps.google.com/?q=place_id:{place_id}",
        observed_at=now,
        values=(
            FieldValue(
                entity_type="company",
                field="name",
                value=name,
                source_key="google_places",
                source_url=f"https://maps.google.com/?q=place_id:{place_id}",
                observed_at=now,
                method="api",
                derivation="found",
                confidence=0.85,
            ),
        ),
    )


class FakeConnectors:
    def __init__(self, found: list[Candidate] | Exception) -> None:
        self._found = found
        self.searches = 0

    def get(self, key: str) -> Any:
        return self

    async def search(self, query: Any, ctx: Any) -> list[Candidate]:
        self.searches += 1
        if isinstance(self._found, Exception):
            raise self._found
        return self._found


class FakeReference:
    async def source_id(self, key: str) -> str:
        return "018f4a9a-0000-7000-8000-0000000000ff"

    async def credits_per_unit(self, meter: str) -> int:
        return {"research_quick": 1, "research_standard": 3, "research_deep": 8}.get(meter, 0)


class FakeGraph:
    """Stores nothing; reports which candidates were new."""

    def __init__(self, new: int | None = None) -> None:
        self._new = new
        self.stored: list[list[Candidate]] = []
        self.workspaces: list[str] = []

    async def store(
        self,
        org_id: str,
        candidates: list[Candidate],
        *,
        source_id: str,
        workspace_id: str,
        research_job_id: str,
    ) -> list[Stored]:
        self.stored.append(list(candidates))
        self.workspaces.append(workspace_id)
        new = len(candidates) if self._new is None else self._new
        return [
            Stored(
                company_id=f"c{i}",
                location_id=f"l{i}",
                lead_id=f"lead{i}",
                is_new=i < new,
                values_written=len(c.values),
            )
            for i, c in enumerate(candidates)
        ]


class FakeTasks:
    def __init__(self, *, claimable: bool = True) -> None:
        self.claimable = claimable
        self.running: list[str] = []
        self.completed: list[dict[str, Any]] = []
        self.failed: list[str] = []
        self.cancelled: list[str] = []

    async def mark_running(self, org_id: str, task_id: str, attempt: int) -> bool:
        self.running.append(task_id)
        return self.claimable

    async def mark_completed(
        self, org_id: str, task_id: str, output: dict[str, Any], cost_micros: int
    ) -> bool:
        self.completed.append(output)
        return True

    async def mark_failed(
        self, org_id: str, task_id: str, error_class: str, cost_micros: int
    ) -> bool:
        self.failed.append(error_class)
        return True

    async def mark_cancelled(self, org_id: str, task_id: str) -> bool:
        self.cancelled.append(task_id)
        return True


class FakeJobs:
    def __init__(self) -> None:
        self.progress: list[dict[str, int]] = []
        self.running = 0
        self.finished = 0

    async def mark_running(self, org_id: str, job_id: str) -> bool:
        self.running += 1
        return True

    async def add_progress(self, org_id: str, job_id: str, counts: dict[str, int]) -> bool:
        self.progress.append(counts)
        return True

    async def finish_if_done(self, org_id: str, job_id: str) -> bool:
        self.finished += 1
        return True

    async def workspace_of(self, org_id: str, job_id: str) -> str | None:
        return WORKSPACE

    async def mark_failed(self, org_id: str, job_id: str, error_class: str, message: str) -> bool:
        return True

    async def mark_cancelled(self, org_id: str, job_id: str) -> bool:
        return True


class FakeUsage:
    def __init__(self, *, accepted: bool = True) -> None:
        self.accepted = accepted
        self.calls: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return self.accepted


def build(
    connectors: FakeConnectors,
    graph: FakeGraph,
    tasks: FakeTasks,
    jobs: FakeJobs,
    usage: FakeUsage,
) -> HandlerRegistry:
    registry = HandlerRegistry()
    register_discovery_handlers(
        registry,
        connectors=connectors,  # type: ignore[arg-type]
        reference=FakeReference(),  # type: ignore[arg-type]
        graph=graph,  # type: ignore[arg-type]
        tasks=tasks,  # type: ignore[arg-type]
        jobs=jobs,  # type: ignore[arg-type]
        usage=usage,  # type: ignore[arg-type]
    )
    return registry


async def run(
    redis: Redis,
    make_envelope: Any,
    *,
    connectors: FakeConnectors,
    graph: FakeGraph | None = None,
    tasks: FakeTasks | None = None,
    jobs: FakeJobs | None = None,
    usage: FakeUsage | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[FakeGraph, FakeTasks, FakeJobs, FakeUsage]:
    graph = graph or FakeGraph()
    tasks = tasks or FakeTasks()
    jobs = jobs or FakeJobs()
    usage = usage or FakeUsage()
    registry = build(connectors, graph, tasks, jobs, usage)
    envelope = make_envelope(
        TASK_TYPE,
        job_id=TASK,
        org_id=ORG,
        research_job_id=JOB,
        budget={"credits_remaining": 50, "cost_cap_micros": 1_000_000},
        payload=payload if payload is not None else PAYLOAD,
    )
    handler = registry.get(TASK_TYPE)
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
    return graph, tasks, jobs, usage


async def test_what_the_search_found_is_stored_and_counted(
    redis: Redis, make_envelope: Any
) -> None:
    connectors = FakeConnectors([candidate("p1", "Alpha Dental"), candidate("p2", "Beta Dental")])

    graph, tasks, jobs, _ = await run(redis, make_envelope, connectors=connectors)

    assert [c.external_id for c in graph.stored[0]] == ["p1", "p2"]
    assert tasks.completed[0]["candidates"] == 2
    assert tasks.completed[0]["leads"] == 2
    assert jobs.progress == [{"candidates": 2, "leads": 2, "values": 2}]


async def test_a_job_pays_for_businesses_it_did_not_already_have(
    redis: Redis, make_envelope: Any
) -> None:
    connectors = FakeConnectors([candidate(f"p{i}", f"Clinic {i}") for i in range(5)])
    # Two of the five are new; the other three were already in the graph.
    graph = FakeGraph(new=2)

    _, tasks, _, usage = await run(redis, make_envelope, connectors=connectors, graph=graph)

    # A job that rediscovers the same shop through five phrasings has found one lead and pays
    # for one. Charging per candidate would bill five times for the same business.
    assert usage.calls[0]["units"] == 2
    assert usage.calls[0]["credits"] == 6  # 2 leads x 3 credits at standard depth
    assert usage.calls[0]["meter"] == "research_standard"
    assert tasks.completed[0]["credits"] == 6


@pytest.mark.parametrize(
    ("depth", "meter", "credits"),
    [
        ("quick", "research_quick", 2),
        ("standard", "research_standard", 6),
        ("deep", "research_deep", 16),
    ],
)
async def test_the_price_of_a_lead_follows_the_depth_the_user_chose(
    redis: Redis, make_envelope: Any, depth: str, meter: str, credits: int
) -> None:
    connectors = FakeConnectors([candidate("p1", "A"), candidate("p2", "B")])

    _, _, _, usage = await run(
        redis,
        make_envelope,
        connectors=connectors,
        payload={**PAYLOAD, "depth": depth},
    )

    # The rate comes from app.credit_rates, not from a constant here: the price of a lead is a
    # commercial decision that changes without a deploy.
    assert usage.calls[0]["meter"] == meter
    assert usage.calls[0]["credits"] == credits


async def test_leads_are_delivered_to_the_workspace_that_asked_for_them(
    redis: Redis, make_envelope: Any
) -> None:
    connectors = FakeConnectors([candidate("p1", "A")])

    graph, _, _, _ = await run(redis, make_envelope, connectors=connectors)

    # Read from the job row, not taken from the envelope: a lead delivered to the wrong
    # workspace is another tenant's data appearing in someone's list.
    assert graph.workspaces == [WORKSPACE]


async def test_a_company_another_customer_found_first_is_still_a_new_lead_here(
    redis: Redis, make_envelope: Any
) -> None:
    connectors = FakeConnectors([candidate("p1", "A"), candidate("p2", "B")])
    # The graph already held both businesses; this workspace had been given neither.
    graph = FakeGraph(new=2)

    _, tasks, _, usage = await run(redis, make_envelope, connectors=connectors, graph=graph)

    # "Delivered new lead" is delivered to a workspace (docs/11, ADR-0012). Counting against the
    # global graph meant a second customer searching the same market received nothing and paid
    # nothing -- they ran a search, we spent their budget, and withheld the answer.
    assert usage.calls[0]["units"] == 2
    assert tasks.completed[0]["leads"] == 2


async def test_a_search_that_found_nothing_new_charges_nothing(
    redis: Redis, make_envelope: Any
) -> None:
    connectors = FakeConnectors([candidate("p1", "Already Known")])

    _, tasks, _, usage = await run(
        redis, make_envelope, connectors=connectors, graph=FakeGraph(new=0)
    )

    # A workspace re-running its own search already holds these leads, so there is nothing to
    # deliver and nothing to charge for.
    assert usage.calls == []
    assert tasks.completed[0]["credits"] == 0
    # The task still succeeded: finding nothing new is a real answer, not a failure.
    assert tasks.failed == []


async def test_reporting_failure_does_not_undo_a_task_that_worked(
    redis: Redis, make_envelope: Any
) -> None:
    class BrokenJobs(FakeJobs):
        async def add_progress(self, org_id: str, job_id: str, counts: dict[str, int]) -> bool:
            raise RuntimeError('syntax error at or near ":"')

    connectors = FakeConnectors([candidate("p1", "A")])
    jobs = BrokenJobs()
    _, tasks, _, usage = await run(redis, make_envelope, connectors=connectors, jobs=jobs)

    # Everything before the counters is paid for and stored. A live Delhi run let a broken
    # counter statement take seventeen such tasks down with it -- each one had already found its
    # businesses, written them, and charged for them.
    assert tasks.completed
    assert tasks.failed == []
    assert usage.calls
    # And the job still gets its chance to finish.
    assert jobs.finished == 1


async def test_a_retry_of_a_finished_task_can_still_close_the_job(
    redis: Redis, make_envelope: Any
) -> None:
    connectors = FakeConnectors([candidate("p1", "A")])
    jobs = FakeJobs()

    _, _, _, usage = await run(
        redis, make_envelope, connectors=connectors, tasks=FakeTasks(claimable=False), jobs=jobs
    )

    # The attempt that finished this task may have died before checking whether it was the last
    # one. If no later attempt ever checks either, the job sits at `running` for ever with the
    # user's credits reserved.
    assert connectors.searches == 0
    assert usage.calls == []
    assert jobs.finished == 1


async def test_a_task_that_already_finished_is_not_run_again(
    redis: Redis, make_envelope: Any
) -> None:
    connectors = FakeConnectors([candidate("p1", "A")])

    _, tasks, _, usage = await run(
        redis, make_envelope, connectors=connectors, tasks=FakeTasks(claimable=False)
    )

    # A redelivery, or a message reclaimed after this task had completed. Running it again would
    # pay for the same search a second time.
    assert connectors.searches == 0
    assert usage.calls == []
    assert tasks.completed == []


async def test_a_cancelled_job_runs_no_search_and_charges_nothing(
    redis: Redis, make_envelope: Any
) -> None:
    await redis.set(CANCEL_KEY.format(job_id=JOB), "1")
    connectors = FakeConnectors([candidate("p1", "A")])

    _, tasks, _, usage = await run(redis, make_envelope, connectors=connectors)

    # The API has already released the credits, so anything spent now is unmetered (ADR-0008),
    # and the task is marked cancelled rather than left running for ever.
    assert connectors.searches == 0
    assert usage.calls == []
    assert tasks.cancelled == [TASK]
    assert tasks.completed == []


async def test_a_failed_search_records_the_failure_before_it_propagates(
    redis: Redis, make_envelope: Any
) -> None:
    connectors = FakeConnectors(TransientError("places is down"))

    with pytest.raises(TransientError):
        await run(redis, make_envelope, connectors=FakeConnectors(TransientError("down")))

    tasks = FakeTasks()
    with pytest.raises(TransientError):
        await run(redis, make_envelope, connectors=connectors, tasks=tasks)

    # The consumer decides whether to retry; the task's own row must say what happened either
    # way, or a job could never finish because finish_if_done waits on it.
    assert tasks.failed == ["transient"]


async def test_a_payload_the_executor_does_not_understand_fails_the_task(
    redis: Redis, make_envelope: Any
) -> None:
    connectors = FakeConnectors([])
    with pytest.raises(InvalidInputError):
        await run(
            redis,
            make_envelope,
            connectors=connectors,
            payload={"source": "google_places"},  # no query
        )
    assert connectors.searches == 0


async def test_one_task_finishing_does_not_end_the_whole_jobs_event_stream(
    redis: Redis, make_envelope: Any
) -> None:
    events: list[dict[str, Any]] = []

    class Recorder(ProgressPublisher):
        async def publish(self, **kwargs: Any) -> Any:  # type: ignore[override]
            events.append(kwargs)
            return None

    connectors = FakeConnectors([candidate("p1", "A")])
    jobs = FakeJobs()
    registry = build(connectors, FakeGraph(), FakeTasks(), jobs, FakeUsage())
    envelope = make_envelope(
        TASK_TYPE,
        job_id=TASK,
        org_id=ORG,
        research_job_id=JOB,
        budget={"credits_remaining": 50, "cost_cap_micros": 1_000_000},
        payload=PAYLOAD,
    )
    handler = registry.get(TASK_TYPE)
    assert handler is not None
    await handler(
        JobContext(
            envelope=envelope,
            redis=redis,
            progress=Recorder(redis),
            log=structlog.get_logger("test"),
            message_id="1-0",
        )
    )

    # The API's SSE stream closes on a terminal status (progress-stream.ts). One task of many
    # finishing must not look like the job finishing.
    assert events and all(e["status"] == "running" for e in events)
    assert events[0]["stage"] == "discovery"
    assert events[0]["job_id"] == JOB, "progress goes to the job's channel, not the task's"
    # Whether the job is actually over is decided by the tasks table, not by this task.
    assert jobs.finished == 1
