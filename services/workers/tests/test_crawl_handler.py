"""`crawl.company_site` end to end (Phase 3).

The first thing in the product that produces an email address, so these tests are about the whole
path: homepage, the pages it leads to, what the values say about where they came from, and the
cases where a site is entitled to say no.
"""

from typing import Any

import httpx
import pytest
import respx
import structlog
from redis.asyncio import Redis

from app.connectors.types import FieldValue
from app.crawl.fetcher import CrawlFetcher, crawl_http_client
from app.crawl.frontier import Frontier
from app.crawl.robots import RobotsPolicy
from app.handlers import crawl as crawl_handler
from app.handlers.crawl import JOB_TYPE, register_crawl_handlers
from app.jobs.cancellation import CANCEL_KEY
from app.jobs.context import JobContext
from app.jobs.errors import InvalidInputError
from app.jobs.progress import ProgressPublisher
from app.jobs.registry import HandlerRegistry
from tests.test_crawl_robots import FakeFetcher, FakeRedis
from tests.test_discovery_handler import FakeJobs, FakeReference, FakeTasks

ORG = "11111111-1111-7111-8111-111111111111"
JOB = "22222222-2222-7222-8222-222222222222"
TASK = "33333333-3333-7333-8333-333333333333"
COMPANY = "44444444-4444-7444-8444-444444444444"
SITE = "https://clinicdelhi.in/"

HOME = b"""
<html><body>
  <h1>Clinic Delhi</h1>
  <nav><a href="/contact">Contact Us</a><a href="/about">About Us</a></nav>
</body></html>
"""

CONTACT = b"""
<html><body>
  <p>Email <a href="mailto:info@clinicdelhi.in">info@clinicdelhi.in</a></p>
  <p>Call <a href="tel:+919876543210">+91 98765 43210</a></p>
  <a href="https://instagram.com/clinicdelhi">Instagram</a>
</body></html>
"""

ABOUT = b"<html><body><p>Serving South Delhi since 1998.</p></body></html>"


class FakeGraph:
    """Records the values a crawl wrote onto an existing company."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[FieldValue]]] = []

    async def store(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise AssertionError("a crawl must not create companies or deliver leads")

    async def add_company_values(
        self, org_id: str, company_id: str, values: Any, *, source_id: str
    ) -> int:
        self.calls.append((org_id, company_id, list(values)))
        return len(list(values))

    @property
    def written(self) -> list[FieldValue]:
        return [v for _, _, values in self.calls for v in values]


def build(
    redis: Redis,
    *,
    robots: bytes = b"User-agent: *\nAllow: /\n",
    robots_status: int = 200,
) -> tuple[HandlerRegistry, FakeGraph, FakeTasks, FakeJobs, Any]:
    http = crawl_http_client()
    policy = RobotsPolicy(FakeRedis(), FakeFetcher(robots_status, robots))
    fetcher = CrawlFetcher(http, policy, resolver=_AllowAll(), pacer=None)
    graph, tasks, jobs = FakeGraph(), FakeTasks(), FakeJobs()
    registry = HandlerRegistry()
    register_crawl_handlers(
        registry,
        fetcher=fetcher,
        frontier=Frontier(redis, cooldown_s=0.0),
        graph=graph,  # type: ignore[arg-type]
        reference=FakeReference(),  # type: ignore[arg-type]
        tasks=tasks,  # type: ignore[arg-type]
        jobs=jobs,  # type: ignore[arg-type]
    )
    return registry, graph, tasks, jobs, http


class _AllowAll:
    def check(self, url: str) -> None:
        return None


PAYLOAD = {"company_id": COMPANY, "website": SITE, "depth": "standard", "country": "IN"}


async def run(
    redis: Redis,
    make_envelope: Any,
    registry: HandlerRegistry,
    payload: dict[str, Any] | None = None,
) -> None:
    envelope = make_envelope(
        JOB_TYPE,
        job_id=TASK,
        org_id=ORG,
        research_job_id=JOB,
        payload=payload if payload is not None else PAYLOAD,
    )
    handler = registry.get(JOB_TYPE)
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


def mock_site() -> None:
    respx.get("https://clinicdelhi.in/").mock(return_value=httpx.Response(200, content=HOME))
    respx.get("https://clinicdelhi.in/contact").mock(
        return_value=httpx.Response(200, content=CONTACT)
    )
    respx.get("https://clinicdelhi.in/about").mock(return_value=httpx.Response(200, content=ABOUT))
    respx.get("https://clinicdelhi.in/sitemap.xml").mock(return_value=httpx.Response(404))


@pytest.mark.asyncio
@respx.mock
async def test_a_crawl_produces_an_email_with_its_provenance(
    redis: Redis, make_envelope: Any
) -> None:
    mock_site()
    registry, graph, _, _, http = build(redis)
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()

    by_field = {v.field: v for v in graph.written}
    assert by_field["email"].value == "info@clinicdelhi.in"
    # The whole point of the product: the value says where it came from.
    assert by_field["email"].source_url == "https://clinicdelhi.in/contact"
    assert by_field["email"].source_key == "website"
    assert by_field["email"].method == "crawl"
    assert by_field["email"].derivation == "found"
    assert by_field["phone"].value == "+919876543210"
    assert by_field["instagram"].value == "clinicdelhi"


@pytest.mark.asyncio
@respx.mock
async def test_it_reads_the_pages_the_homepage_points_at(redis: Redis, make_envelope: Any) -> None:
    mock_site()
    registry, _, tasks, _, http = build(redis)
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()
    [output] = tasks.completed
    # Homepage, contact, about.
    assert output["pages_read"] == 3
    # The absent sitemap is not a page of the site, so it is not counted as refused.
    assert output["pages_restricted"] == 0
    assert "email" in output["fields"]


@pytest.mark.asyncio
@respx.mock
async def test_nothing_is_charged_and_no_lead_is_delivered(
    redis: Redis, make_envelope: Any
) -> None:
    # The business was found and paid for by a search. This is its own site telling us more, which
    # costs nothing -- and FakeGraph raises if `store()` is called, so a lead cannot be delivered.
    mock_site()
    registry, _, tasks, _, http = build(redis)
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()
    assert tasks.completed[0].get("credits") is None


@pytest.mark.asyncio
@respx.mock
async def test_a_site_that_turns_us_away_completes_rather_than_fails(
    redis: Redis, make_envelope: Any
) -> None:
    # A 403 is the site exercising its rights. Retrying it three times and then marking the job
    # broken would be both rude and wrong.
    respx.get("https://clinicdelhi.in/").mock(return_value=httpx.Response(403))
    registry, graph, tasks, _, http = build(redis)
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()
    assert tasks.failed == []
    assert tasks.completed[0]["pages_restricted"] == 1
    assert tasks.completed[0]["values"] == 0
    assert graph.written == []


@pytest.mark.asyncio
@respx.mock
async def test_a_robots_disallow_is_obeyed_and_nothing_is_fetched(
    redis: Redis, make_envelope: Any
) -> None:
    route = respx.get("https://clinicdelhi.in/").mock(
        return_value=httpx.Response(200, content=HOME)
    )
    registry, graph, tasks, _, http = build(redis, robots=b"User-agent: *\nDisallow: /\n")
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()
    assert not route.called
    assert tasks.failed == []
    assert graph.written == []


@pytest.mark.asyncio
@respx.mock
async def test_a_cancelled_job_stops_before_reading_anything(
    redis: Redis, make_envelope: Any
) -> None:
    await redis.set(CANCEL_KEY.format(job_id=JOB), "1")
    route = respx.get("https://clinicdelhi.in/").mock(
        return_value=httpx.Response(200, content=HOME)
    )
    registry, graph, tasks, _, http = build(redis)
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()
    assert not route.called
    assert tasks.cancelled == [TASK]
    assert graph.written == []


@pytest.mark.asyncio
@respx.mock
async def test_a_redelivered_task_does_not_crawl_the_site_twice(
    redis: Redis, make_envelope: Any
) -> None:
    mock_site()
    registry, graph, tasks, jobs, http = build(redis)
    tasks.claimable = False
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()
    assert graph.written == []
    # It still asks whether the job is over: the attempt that finished this task may have died
    # before it got to check.
    assert jobs.finished == 1


@pytest.mark.asyncio
@respx.mock
async def test_a_quick_crawl_reads_only_the_homepage(redis: Redis, make_envelope: Any) -> None:
    mock_site()
    registry, _, tasks, _, http = build(redis)
    try:
        await run(
            redis,
            make_envelope,
            registry,
            {"company_id": COMPANY, "website": SITE, "depth": "quick", "country": "IN"},
        )
    finally:
        await http.aclose()
    assert tasks.completed[0]["pages_read"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_a_payload_the_handler_does_not_understand_fails_as_invalid_input(
    redis: Redis, make_envelope: Any
) -> None:
    registry, _, _, _, http = build(redis)
    try:
        with pytest.raises(InvalidInputError, match="invalid crawl payload"):
            await run(redis, make_envelope, registry, {"company_id": COMPANY})
    finally:
        await http.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_progress_failing_does_not_undo_a_finished_crawl(
    redis: Redis, make_envelope: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A live Delhi run marked seventeen successful, charged discovery tasks failed because a
    # counter update threw. The same ordering is kept here, and tested.
    mock_site()
    registry, graph, tasks, jobs, http = build(redis)

    async def boom(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("counters are down")

    monkeypatch.setattr(jobs, "add_progress", boom)
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()
    assert tasks.failed == []
    assert len(tasks.completed) == 1
    assert graph.written != []


def test_the_handler_registers_under_the_documented_job_type() -> None:
    assert crawl_handler.JOB_TYPE == "crawl.company_site"
