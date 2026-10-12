"""Spec in, leads out: the whole chain, in one file.

This exists because of how every filter bug in this project has been found -- by the owner
running a search on the deployed site and reading the result. The unit tests were green the
whole time, and they were not wrong: each piece did its own job correctly. What nothing tested
was the chain between them.

`filters.has_website` was parsed, validated, stored on the spec, shown as a chip in the UI,
listed in the feasibility table as applied -- and read by no code between the spec and the lead.
A live run delivered 225 restaurants of which 50 had a website. `filters.contact` was the same,
and so were the three `keywords` entries. None of it was visible to a suite that tests the
index, the connector, the planner and the handler separately.

So this builds a real `ResearchSpec`, runs the real planner over it, and hands its task to the
real discovery handler, through the real connector, over a real SQLite place index. Then it asks
one question: did the businesses that came out match what the spec asked for?

The graph is a fake here on purpose. What reached storage is the question this file is about,
and storage itself is covered against a live database in `test_graph_batch.py`. Keeping this one
credential-free matters -- a test that runs only when someone exports a database URL is a test
that will not run.

`test_every_runnable_filter_has_an_end_to_end_case` closes the loop: a filter whose feasibility
mode claims it changes what the user gets must appear in `CASES` below, or the suite fails.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import structlog
from redis.asyncio import Redis

from app.ai.feasibility import FILTER_MODES
from app.handlers.discovery import register_discovery_handlers
from app.jobs.context import JobContext
from app.jobs.progress import ProgressPublisher
from app.jobs.registry import HandlerRegistry
from app.places.index import IndexWriter
from app.places.model import OVERTURE, Place
from app.planner.capability import build_capabilities
from app.planner.plan import Expansion, build_plan
from app.planner.templates import template_for
from tests.planner_support import FakeReference, make_registry, make_spec
from tests.test_discovery_handler import (
    ORG,
    FakeGraph,
    FakeJobs,
    FakeTasks,
    FakeUsage,
)
from tests.test_discovery_handler import (
    FakeReference as HandlerReference,
)

JOB = "018f4a9a-0000-7000-8000-00000000aa01"
TASK = "018f4a9a-0000-7000-8000-00000000aa02"

#: What the index holds, chosen so that every filter under test separates them.
#:
#:   name              website  phone  email  instagram
#:   Site And Phone       y        y      -       y
#:   Site Only            y        -      -       -
#:   Phone Only           -        y      -       y
#:   Email Only           -        -      y       -
#:
#: All four are `dental_clinic`, inside Pune's box, because that is what the seeded fakes in
#: `planner_support` know: the industry `dental-clinics` maps to the Overture category
#: `dental_clinic`, and Pune is a city the fake geography can find.
BUSINESSES: tuple[tuple[str, str | None, str | None, str | None, tuple[str, ...]], ...] = (
    (
        "Site And Phone",
        "https://a.example.in",
        "+911141000001",
        None,
        ("https://www.instagram.com/siteandphone",),
    ),
    ("Site Only", "https://b.example.in", None, None, ()),
    ("Phone Only", None, "+911141000002", None, ("https://www.instagram.com/phoneonly",)),
    ("Email Only", None, None, "d@example.in", ()),
)

EVERYTHING = ["Email Only", "Phone Only", "Site And Phone", "Site Only"]


@pytest.fixture
def index_path(tmp_path: Path) -> Path:
    path = tmp_path / "e2e.sqlite"
    with IndexWriter(path) as writer:
        writer.add(
            [
                Place(
                    source=OVERTURE,
                    external_id=f"o{i}",
                    name=name,
                    category="dental_clinic",
                    categories=("health_and_medical", "dental_clinic"),
                    lat=18.50 + i / 100,
                    lon=73.85,
                    website=website,
                    phone=phone,
                    email=email,
                    socials=socials,
                    city="Pune",
                    country="IN",
                )
                for i, (name, website, phone, email, socials) in enumerate(BUSINESSES)
            ]
        )
        writer.finish(extract_date=datetime(2026, 9, 23, tzinfo=UTC))
    return path


async def delivered(
    redis: Redis, make_envelope: Any, index_path: Path, **spec_filters: Any
) -> list[str]:
    """The names a spec actually delivers, from `ResearchSpec` to what reaches the graph."""
    spec = make_spec(cities=["Pune"], taxonomy_ids=["dental-clinics"], **spec_filters)

    # The place index is the only source in the registry, so the plan has to choose it. With
    # Google Places present the planner would pick whichever is cheaper and this test would be
    # measuring the wrong connector without saying so.
    registry = make_registry(places=False, serp=False, index_path=index_path)
    plan = await build_plan(
        spec,
        capabilities=build_capabilities(registry, ["name", "phone", "website"]),
        template=template_for("prospecting"),
        reference=FakeReference(),
        expansion=Expansion(queries=("dental clinics in Pune",)),
        credits=500,
    )
    assert plan.tasks, f"the planner produced no task for {spec_filters}"

    graph = FakeGraph()
    handlers = HandlerRegistry()
    register_discovery_handlers(
        handlers,
        connectors=registry,
        reference=HandlerReference(),  # type: ignore[arg-type]
        graph=graph,  # type: ignore[arg-type]
        tasks=FakeTasks(),  # type: ignore[arg-type]
        jobs=FakeJobs(),  # type: ignore[arg-type]
        usage=FakeUsage(),  # type: ignore[arg-type]
    )

    task = plan.tasks[0]
    handler = handlers.get(task.type)
    assert handler is not None, f"nothing handles {task.type}"
    await handler(
        JobContext(
            envelope=make_envelope(
                task.type,
                job_id=TASK,
                org_id=ORG,
                research_job_id=JOB,
                budget={"credits_remaining": 500, "cost_cap_micros": 10_000_000},
                payload=task.input,
                attempt=1,
            ),
            redis=redis,
            progress=ProgressPublisher(redis),
            log=structlog.get_logger("e2e"),
            message_id="1-0",
        )
    )
    return sorted(c.name for batch in graph.stored for c in batch)


async def test_a_spec_with_no_narrowing_filter_delivers_the_whole_market(
    redis: Redis, make_envelope: Any, index_path: Path
) -> None:
    """The baseline. Without it, a filter that returns nothing looks like it is working."""
    assert await delivered(redis, make_envelope, index_path) == EVERYTHING


async def test_asking_for_a_website_delivers_only_those_with_one(
    redis: Redis, make_envelope: Any, index_path: Path
) -> None:
    """The bug, end to end. Every piece of this passed its own test while the chain did
    nothing: the index could filter, the connector could pass an option, the planner could
    write a payload, and no line joined them."""
    assert await delivered(redis, make_envelope, index_path, has_website=True) == [
        "Site And Phone",
        "Site Only",
    ]


async def test_asking_for_no_website_delivers_only_those_without(
    redis: Redis, make_envelope: Any, index_path: Path
) -> None:
    """`False` is a filter, not an absence -- the market of businesses to sell a website to."""
    assert await delivered(redis, make_envelope, index_path, has_website=False) == [
        "Email Only",
        "Phone Only",
    ]


async def test_asking_for_a_phone_delivers_only_those_reachable_by_phone(
    redis: Redis, make_envelope: Any, index_path: Path
) -> None:
    assert await delivered(redis, make_envelope, index_path, contact_any_of=["phone"]) == [
        "Phone Only",
        "Site And Phone",
    ]


async def test_asking_for_a_phone_or_an_email_delivers_either(
    redis: Redis, make_envelope: Any, index_path: Path
) -> None:
    """`any_of`, so one channel is enough."""
    assert await delivered(
        redis, make_envelope, index_path, contact_any_of=["phone", "email"]
    ) == ["Email Only", "Phone Only", "Site And Phone"]


async def test_a_channel_discovery_cannot_see_narrows_nothing(
    redis: Redis, make_envelope: Any, index_path: Path
) -> None:
    """Returning nothing would be worse than returning everything: an empty result reads as
    "no such businesses" rather than "we cannot check that". `ai/feasibility.py` says which."""
    assert (
        await delivered(redis, make_envelope, index_path, contact_any_of=["whatsapp"])
        == EVERYTHING
    )


async def test_asking_for_a_social_profile_delivers_only_those_that_have_one(
    redis: Redis, make_envelope: Any, index_path: Path
) -> None:
    """No source can be asked "has an Instagram", so this is a delivery filter only.

    Judged by the same `FieldValue`s the mapping wrote, so the filter and the stored value
    cannot have two different ideas of what an Instagram link is.
    """
    assert await delivered(redis, make_envelope, index_path, social_required=["instagram"]) == [
        "Phone Only",
        "Site And Phone",
    ]


async def test_asking_for_two_platforms_requires_both(
    redis: Redis, make_envelope: Any, index_path: Path
) -> None:
    """`filters.social` is keyed by platform, so naming two is asking for both -- the opposite
    of `contact.any_of`, where one channel is enough. Nothing here has a Facebook page."""
    assert (
        await delivered(
            redis, make_envelope, index_path, social_required=["instagram", "facebook"]
        )
        == []
    )


async def test_two_filters_together_narrow_to_their_intersection(
    redis: Redis, make_envelope: Any, index_path: Path
) -> None:
    """Each working alone is not the same as both working together, and the chain is where one
    quietly overwriting the other would show up."""
    assert await delivered(
        redis, make_envelope, index_path, has_website=True, contact_any_of=["phone"]
    ) == ["Site And Phone"]


# ---------------------------------------------------------------- the loop this file closes

#: Filter path -> the test above that exercises it from spec to lead. A name, not a reference,
#: because the point is that a human adding a filter has to put something here.
CASES: dict[str, str] = {
    "filters.industry": "test_a_spec_with_no_narrowing_filter_delivers_the_whole_market",
    "filters.location": "test_a_spec_with_no_narrowing_filter_delivers_the_whole_market",
    "filters.has_website": "test_asking_for_a_website_delivers_only_those_with_one",
    "filters.contact": "test_asking_for_a_phone_delivers_only_those_reachable_by_phone",
    "filters.social": "test_asking_for_a_social_profile_delivers_only_those_that_have_one",
    # Not a narrowing filter: a lead the workspace already holds is still delivered, it is
    # simply not charged for again. That is billing, covered in test_discovery_handler.py.
    "exclude.existing_leads": "test_discovery_handler.py: a lead already held is not billed",
}


def test_every_runnable_filter_has_an_end_to_end_case() -> None:
    """A filter whose mode claims it changes the result must be exercised through the chain.

    This is the test that makes the next `has_website` impossible to ship quietly. Declaring a
    filter `post_filter` or `directly_searchable` in `FILTER_MODES` without an entry here fails
    the suite, and the entry has to name a test that runs the whole way through.
    """
    runnable = sorted(
        path
        for path, (mode, _) in FILTER_MODES.items()
        if mode in ("directly_searchable", "post_filter")
    )
    missing = [path for path in runnable if path not in CASES]
    assert missing == [], f"these claim to run but have no end-to-end case: {missing}"


def test_each_named_case_is_a_test_in_this_file_or_says_where_it_lives() -> None:
    """So `CASES` cannot be satisfied with a name nobody wrote."""
    source = Path(__file__).read_text(encoding="utf-8")
    for path, case in CASES.items():
        if case.endswith(".py") or ": " in case:
            continue  # points at another file, which names itself
        assert f"async def {case}(" in source or f"def {case}(" in source, (
            f"{path} names {case}, which is not a test in this file"
        )
