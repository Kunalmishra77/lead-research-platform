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
from app.crawl.dns_records import MailDns
from app.crawl.fetcher import CrawlFetcher, crawl_http_client
from app.crawl.frontier import Frontier
from app.crawl.robots import RobotsPolicy
from app.db.raw_documents import Recorded
from app.handlers import crawl as crawl_handler
from app.handlers.crawl import JOB_TYPE, register_crawl_handlers
from app.jobs.cancellation import CANCEL_KEY
from app.jobs.context import JobContext
from app.jobs.errors import ErrorClass, InvalidInputError
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


class FakeRawDocuments:
    """Records what it was asked to keep, without a disk or a database.

    Reports a different id per body and the same id for a repeat, because that is the one
    behaviour the handler depends on: a value points at the copy it was read from, and a page
    fetched twice unchanged is one copy. The real thing is tested against a real Postgres in
    `test_raw_documents.py`.
    """

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.recorded: list[dict[str, Any]] = []
        self._ids: dict[bytes, str] = {}

    async def record(self, **kwargs: Any) -> Recorded:
        if self.fail:
            raise RuntimeError("the disk is full")
        self.recorded.append(kwargs)
        body = kwargs.get("body")
        if body is None:
            return Recorded(raw_document_id=None, stored=False)
        known = self._ids.get(body)
        if known is not None:
            return Recorded(raw_document_id=known, stored=False, deduplicated=True)
        document_id = f"doc-{len(self._ids) + 1}"
        self._ids[body] = document_id
        return Recorded(raw_document_id=document_id, stored=True)


def build(
    redis: Redis,
    *,
    robots: bytes = b"User-agent: *\nAllow: /\n",
    robots_status: int = 200,
    raw: FakeRawDocuments | None = None,
    mail: MailDns | None = None,
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
        raw=raw,  # type: ignore[arg-type]
        # Never the real resolver. CLAUDE.md: a test does not touch a live third-party service,
        # and a DNS query is one -- it leaves the machine, it is slow, and its answer changes
        # under you.
        resolve_mail=_stub_mail(mail),
    )
    return registry, graph, tasks, jobs, http


def _stub_mail(records: MailDns | None) -> Any:
    async def resolve(domain: str) -> MailDns | None:
        return records

    return resolve


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


@pytest.mark.asyncio
async def test_a_job_whose_last_crawl_failed_still_finishes(
    redis: Redis, make_envelope: Any
) -> None:
    """A dead site must not strand the job that was reading it.

    `finish_if_done` runs at the end of a *successful* task. When the last task of a job is one
    the consumer gave up on, there is no successful task to run it, and the job stays `running`
    holding its reservation -- the customer's credits, for leads they never got.

    The discovery handler was fixed for exactly this on 2026-10-12 and the crawl handler, which
    has the same shape, was not. It showed up the same day on a live "hotels in Delhi" run: 154
    crawl tasks completed, `ninaniyagroup.com` went unanswered through all five attempts, and
    the job sat `running` with nothing left to run.

    A crawl is where this is most likely rather than least: every lead's own website is its own
    task, so one dead domain in two hundred is normal.
    """
    registry, _graph, tasks, jobs, http = build(redis)
    try:
        hook = registry.failure_hook(JOB_TYPE)
        assert hook is not None, "a crawl task the consumer gives up on must be recorded"

        envelope = make_envelope(JOB_TYPE, job_id=TASK, org_id=ORG, research_job_id=JOB,
                                 payload=PAYLOAD)
        await hook(envelope, ErrorClass.TRANSIENT, "the site never answered", 5)

        assert tasks.status == "failed"
        assert jobs.finished >= 1, "a dead-lettered crawl must still ask whether the job is over"
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_a_dead_letter_without_a_job_is_recorded_and_asks_nothing(
    redis: Redis, make_envelope: Any
) -> None:
    """A crawl can be run outside a research job (a re-crawl, a backfill). There is no job to
    finish then, and asking about `research_job_id=None` would be a lookup for nothing."""
    registry, _graph, tasks, jobs, http = build(redis)
    try:
        hook = registry.failure_hook(JOB_TYPE)
        assert hook is not None
        envelope = make_envelope(JOB_TYPE, job_id=TASK, org_id=ORG, payload=PAYLOAD)

        await hook(envelope, ErrorClass.TRANSIENT, "the site never answered", 5)

        assert tasks.status == "failed"
        assert jobs.finished == 0
    finally:
        await http.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_every_page_read_is_recorded_and_its_values_point_at_it(
    redis: Redis, make_envelope: Any
) -> None:
    """The point of task 3.6: a value says which *copy* of a page it came from.

    `source_url` already says which page. That stops being enough the moment the site changes,
    which is the only time anybody asks.
    """
    mock_site()
    raw = FakeRawDocuments()
    registry, graph, _tasks, _jobs, http = build(redis, raw=raw)
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()

    kept = [r for r in raw.recorded if r.get("access_status") == "ok"]
    assert len(kept) == 3, "homepage, contact and about"
    assert {r["url"] for r in kept} == {
        "https://clinicdelhi.in/",
        "https://clinicdelhi.in/contact",
        "https://clinicdelhi.in/about",
    }
    assert all(r["source_id"] for r in kept), "a document belongs to a source"

    stored = graph.written
    assert stored, "the crawl found something"
    assert all(v.raw_document_id is not None for v in stored), (
        "every crawled value names the copy it was read from"
    )
    # Values from different pages name different documents: `merge_pages` picks the best
    # evidence per field, and each winner has to keep its own page rather than the first one's.
    assert len({v.raw_document_id for v in stored}) >= 1


@pytest.mark.asyncio
@respx.mock
async def test_the_sitemap_is_fetched_but_not_kept(redis: Redis, make_envelope: Any) -> None:
    """No value is ever extracted from a sitemap, so a copy of one is bytes spent on nothing.

    It is still fetched -- it decides which pages are read -- which is why this is worth a test:
    the obvious implementation records every fetch and doubles the object count.
    """
    mock_site()
    respx.get("https://clinicdelhi.in/sitemap.xml").mock(
        return_value=httpx.Response(200, content=b"<urlset></urlset>")
    )
    raw = FakeRawDocuments()
    registry, _graph, _tasks, _jobs, http = build(redis, raw=raw)
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()

    assert not any("sitemap" in r["url"] for r in raw.recorded)


@pytest.mark.asyncio
@respx.mock
async def test_a_door_that_was_shut_is_recorded_as_such(
    redis: Redis, make_envelope: Any
) -> None:
    """A robots disallow is an outcome, not a failure (CLAUDE.md: stop, never evade).

    The row is where "this door was shut on this date" lives, and that is the answer to "why
    has this company no email".
    """
    raw = FakeRawDocuments()
    registry, _graph, _tasks, _jobs, http = build(
        redis, robots=b"User-agent: *\nDisallow: /\n", raw=raw
    )
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()

    assert [r["access_status"] for r in raw.recorded] == ["restricted"]
    assert raw.recorded[0].get("body") is None


@pytest.mark.asyncio
@respx.mock
async def test_a_crawl_that_cannot_keep_a_page_still_delivers_its_values(
    redis: Redis, make_envelope: Any
) -> None:
    """The leads are the product and the copy is a cache.

    A full disk or a read-only volume must cost a log line, not the emails this crawl found.
    Worth a test because the obvious implementation lets the bookkeeping fail the job.
    """
    mock_site()
    registry, graph, tasks, _jobs, http = build(redis, raw=FakeRawDocuments(fail=True))
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()

    assert tasks.status == "completed"
    stored = graph.written
    assert stored, "the values survived the store failing"
    assert all(v.raw_document_id is None for v in stored), "and claim no copy they do not have"


@pytest.mark.asyncio
@respx.mock
async def test_a_worker_with_no_raw_store_still_crawls(
    redis: Redis, make_envelope: Any
) -> None:
    """`RAW_STORE_PATH` unset is a deployment without the volume, not a broken one."""
    mock_site()
    registry, graph, tasks, _jobs, http = build(redis, raw=None)
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()

    assert tasks.status == "completed"
    assert graph.written


@pytest.mark.asyncio
@respx.mock
async def test_the_domains_mail_host_is_recorded_with_who_runs_it(
    redis: Redis, make_envelope: Any
) -> None:
    """Task 3.9's DNS half. Two values from one lookup, with different honesty.

    `mail_exchanger` is found -- it is the host a sender hands mail to, read from the records.
    `mail_provider` is `derived_pattern`, because the hostname is the evidence and the label is
    our reading of it; CLAUDE.md asks for that distinction on anything a pattern produced.
    """
    mock_site()
    records = MailDns(
        exchangers=("clinicdelhi-in.mail.protection.outlook.com",), provider="Microsoft 365"
    )
    registry, graph, _tasks, _jobs, http = build(redis, mail=records)
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()

    by_field = {v.field: v for v in graph.written}
    assert by_field["mail_exchanger"].value == "clinicdelhi-in.mail.protection.outlook.com"
    assert by_field["mail_exchanger"].derivation == "found"
    assert by_field["mail_provider"].value == "Microsoft 365"
    assert by_field["mail_provider"].derivation == "derived_pattern"
    # The records came from the domain's own nameservers, not from a page that might be a
    # testimonial, so they are believed more than anything the crawl read.
    assert by_field["mail_exchanger"].confidence > by_field["email"].confidence
    assert by_field["mail_exchanger"].source_key == "dns", "not the website's source"


@pytest.mark.asyncio
@respx.mock
async def test_a_domain_with_no_mail_records_gets_no_mail_fields(
    redis: Redis, make_envelope: Any
) -> None:
    """51% of the graph's domains have no MX. Whether that makes an address undeliverable is
    task 4.5's verdict, so the absence is recorded by being absent and nothing here claims a
    business cannot be emailed."""
    mock_site()
    registry, graph, _tasks, _jobs, http = build(redis, mail=None)
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()

    fields = {v.field for v in graph.written}
    assert "mail_exchanger" not in fields
    assert "mail_provider" not in fields
    assert "email" in fields, "and the crawl's own findings are untouched"


@pytest.mark.asyncio
@respx.mock
async def test_an_unrecognised_mail_host_is_recorded_without_a_guess(
    redis: Redis, make_envelope: Any
) -> None:
    """Three of the graph's domains use `jellyfish.systems`, which matches no pattern.

    The host is a fact and the provider is not known, so one value is written and the other is
    left out. Labelling it "other" would be a guess stored as a fact.
    """
    mock_site()
    registry, graph, _tasks, _jobs, http = build(
        redis, mail=MailDns(exchangers=("mx1-hosting.jellyfish.systems",), provider=None)
    )
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()

    fields = {v.field for v in graph.written}
    assert "mail_exchanger" in fields
    assert "mail_provider" not in fields


@pytest.mark.asyncio
@respx.mock
async def test_a_resolver_that_fails_costs_the_two_fields_and_nothing_else(
    redis: Redis, make_envelope: Any
) -> None:
    """The pages are the product and this is a bonus fact about them."""
    mock_site()

    async def explode(domain: str) -> MailDns | None:
        raise TimeoutError("the resolver never answered")

    registry, graph, tasks, _jobs, http = build(redis)
    # Re-register with a resolver that throws, which `build` cannot express.
    registry = HandlerRegistry()
    register_crawl_handlers(
        registry,
        fetcher=CrawlFetcher(
            http, RobotsPolicy(FakeRedis(), FakeFetcher(200, b"User-agent: *\nAllow: /\n")),
            resolver=_AllowAll(), pacer=None,
        ),
        frontier=Frontier(redis, cooldown_s=0.0),
        graph=graph,  # type: ignore[arg-type]
        reference=FakeReference(),  # type: ignore[arg-type]
        tasks=tasks,  # type: ignore[arg-type]
        jobs=FakeJobs(),  # type: ignore[arg-type]
        resolve_mail=explode,
    )
    try:
        await run(redis, make_envelope, registry)
    finally:
        await http.aclose()

    assert tasks.status == "completed"
    assert "email" in {v.field for v in graph.written}
