"""BaseConnector contract + registry (docs/08).

`ExampleConnector` is the shape `/new-connector` scaffolds: declared policy attributes, a
fixture-backed `search`/`fetch`, and a pure `map()` that attaches provenance to every value.
"""

import json
from datetime import UTC, datetime
from typing import ClassVar
from uuid import uuid4

import pytest
import structlog
from redis.asyncio import Redis

from app.connectors import (
    BaseConnector,
    Candidate,
    ConnectorRegistry,
    DiscoveryQuery,
    FieldValue,
    RateLimit,
    RawResult,
    SourceRef,
)
from app.connectors.types import AuthKind, TosClass
from app.jobs.context import JobContext
from app.jobs.errors import InvalidInputError
from app.jobs.progress import ProgressPublisher
from app.metering.context import CallContext
from tests.conftest import EnvelopeFactory
from tests.fixtures import load_json

CTX = CallContext(
    org_id="11111111-1111-7111-8111-111111111111",
    research_job_id="22222222-2222-7222-8222-222222222222",
    trace_id="0af7651916cd43dd8448eb211c80319c",
    log=structlog.get_logger("test"),
)


class ExampleConnector(BaseConnector):
    key: ClassVar[str] = "example_source"
    tos_class: ClassVar[TosClass] = "green"
    auth: ClassVar[AuthKind] = "api_key"
    fields_provided: ClassVar[frozenset[str]] = frozenset(
        {"company.name", "company.phone", "company.website", "company.address"}
    )
    default_ttl_days: ClassVar[int] = 30
    rate_limit: ClassVar[RateLimit] = RateLimit(requests=10, per_seconds=1.0, concurrency=2)
    cost_per_call_micros: ClassVar[int] = 4000

    def __init__(self, case: str = "search_success") -> None:
        self._case = case

    async def search(self, query: DiscoveryQuery, ctx: CallContext) -> list[Candidate]:
        payload = load_json(self.key, self._case)
        return [
            Candidate(
                source_key=self.key,
                external_id=item["id"],
                name=item["name"],
                url=item["website"],
                phone=item["phone"],
                address=item["address"],
                city=item["city"],
                country=item["country"],
                lat=item["location"]["lat"],
                lng=item["location"]["lng"],
                source_url=f"https://api.example.test/places/{item['id']}",
                raw=item,
            )
            for item in payload["results"][: query.max_results]
        ]

    async def fetch(self, ref: SourceRef, ctx: CallContext) -> RawResult:
        payload = load_json(self.key, self._case)
        body = next(i for i in payload["results"] if i["id"] == ref.external_id)
        url = f"https://api.example.test/places/{ref.external_id}"
        return RawResult(
            ref=ref,
            status=200,
            body=json.dumps(body).encode("utf-8"),
            content_type="application/json",
            fetched_at=datetime.now(UTC),
            url=url,
        )

    def map(self, raw: RawResult) -> list[FieldValue]:
        item = json.loads(raw.body)
        return [
            FieldValue(
                entity_type="company",
                field=field,
                value=value,
                source_key=self.key,
                source_url=raw.url,
                observed_at=raw.fetched_at,
                method="api",
                confidence=0.95,
            )
            for field, value in (
                ("company.name", item["name"]),
                ("company.phone", item["phone"]),
                ("company.website", item["website"]),
            )
            if value is not None
        ]


class FreeDirectoryConnector(ExampleConnector):
    key: ClassVar[str] = "free_directory"
    auth: ClassVar[AuthKind] = "none"
    fields_provided: ClassVar[frozenset[str]] = frozenset({"company.name", "company.website"})
    cost_per_call_micros: ClassVar[int] = 0


class ScrapedConnector(ExampleConnector):
    key: ClassVar[str] = "scraped_source"
    tos_class: ClassVar[TosClass] = "red"
    fields_provided: ClassVar[frozenset[str]] = frozenset({"company.name"})
    cost_per_call_micros: ClassVar[int] = 100


@pytest.fixture
def registry() -> ConnectorRegistry:
    reg = ConnectorRegistry()
    reg.register(ExampleConnector())
    reg.register(FreeDirectoryConnector())
    reg.register(ScrapedConnector())
    return reg


def test_the_meter_name_defaults_to_the_source_key() -> None:
    assert ExampleConnector.meter == "api_example_source"
    assert FreeDirectoryConnector.meter == "api_free_directory"


def test_a_connector_missing_a_declared_attribute_is_rejected_at_import_time() -> None:
    with pytest.raises(TypeError, match="missing connector attributes"):

        class Broken(BaseConnector):
            key: ClassVar[str] = "broken"
            tos_class: ClassVar[TosClass] = "green"

            async def search(self, query: DiscoveryQuery, ctx: CallContext) -> list[Candidate]:
                return []

            async def fetch(  # pragma: no cover - never built
                self, ref: SourceRef, ctx: CallContext
            ) -> RawResult:
                raise NotImplementedError

            def map(self, raw: RawResult) -> list[FieldValue]:  # pragma: no cover - never built
                raise NotImplementedError


def test_red_sources_stay_off_until_legal_approves() -> None:
    assert ScrapedConnector.usable_in_production(legal_approved=False) is False
    assert ScrapedConnector.usable_in_production(legal_approved=True) is True
    assert ExampleConnector.usable_in_production(legal_approved=False) is True


async def test_health_reports_the_connector_key() -> None:
    health = await ExampleConnector().health()
    assert health.key == "example_source"
    assert health.ok is True
    assert health.checked_at is not None


def test_the_registry_refuses_a_duplicate_key(registry: ConnectorRegistry) -> None:
    with pytest.raises(ValueError, match="already registered"):
        registry.register(ExampleConnector())


def test_an_unknown_key_fails_loudly(registry: ConnectorRegistry) -> None:
    with pytest.raises(KeyError, match="unknown connector"):
        registry.get("nope")


def test_providing_returns_the_cheapest_allowed_source_first(registry: ConnectorRegistry) -> None:
    # scraped_source is cheaper than example_source but red, so it is not offered by default.
    assert [c.key for c in registry.providing("company.name")] == [
        "free_directory",
        "example_source",
    ]
    assert [c.key for c in registry.providing("company.phone")] == ["example_source"]
    assert registry.providing("company.employee_count") == []


def test_a_red_source_is_only_offered_once_legal_approves(registry: ConnectorRegistry) -> None:
    approved = registry.providing("company.name", legal_approved=True)
    assert [c.key for c in approved] == ["free_directory", "scraped_source", "example_source"]


def test_enabled_keeps_only_registered_and_allowed_sources(registry: ConnectorRegistry) -> None:
    keys = ["example_source", "not_installed", "scraped_source", "free_directory"]
    # An org enabling a red source is not enough on its own.
    assert [c.key for c in registry.enabled(keys)] == ["example_source", "free_directory"]
    assert [c.key for c in registry.enabled(keys, legal_approved=True)] == [
        "example_source",
        "scraped_source",
        "free_directory",
    ]


def test_the_registry_is_iterable_and_lists_its_keys(registry: ConnectorRegistry) -> None:
    assert len(registry) == 3
    assert registry.keys == ["example_source", "free_directory", "scraped_source"]
    assert "example_source" in registry
    assert {c.key for c in registry} == set(registry.keys)


async def test_search_maps_a_recorded_response_to_candidates() -> None:
    candidates = await ExampleConnector().search(DiscoveryQuery(text="restaurants in Delhi"), CTX)
    assert [c.name for c in candidates] == ["Shree Ganesh Restaurant", "Delhi Darbar"]
    first = candidates[0]
    assert first.source_key == "example_source"
    assert first.phone == "+911123456789"
    assert first.source_url.endswith("/places/ex-1")


async def test_max_results_is_respected() -> None:
    candidates = await ExampleConnector().search(DiscoveryQuery(text="cafes", max_results=1), CTX)
    assert len(candidates) == 1


async def test_an_empty_source_response_yields_no_candidates() -> None:
    candidates = await ExampleConnector("search_empty").search(DiscoveryQuery(text="cafes"), CTX)
    assert candidates == []


async def test_every_mapped_value_carries_provenance() -> None:
    connector = ExampleConnector()
    raw = await connector.fetch(SourceRef(source_key=connector.key, external_id="ex-1"), CTX)
    values = connector.map(raw)

    assert {v.field for v in values} == {"company.name", "company.phone", "company.website"}
    for value in values:
        assert value.source_key == "example_source"
        assert value.source_url.endswith("/places/ex-1")
        assert value.method == "api"
        assert 0 < value.confidence <= 1
        assert value.observed_at.tzinfo is not None


async def test_map_is_deterministic_and_skips_missing_values() -> None:
    connector = ExampleConnector()
    raw = await connector.fetch(SourceRef(source_key=connector.key, external_id="ex-2"), CTX)
    first = connector.map(raw)
    second = connector.map(raw)

    assert first == second
    # ex-2 has no website in the fixture, so no value is invented for it.
    assert "company.website" not in {v.field for v in first}


async def test_a_connector_context_carries_the_job_attribution(
    make_envelope: EnvelopeFactory, redis: Redis
) -> None:
    envelope = make_envelope(
        "research.discover",
        org_id=str(uuid4()),
        research_job_id=str(uuid4()),
        budget={"credits_remaining": 50, "cost_cap_micros": 250_000},
    )
    job_ctx = JobContext(
        envelope=envelope,
        redis=redis,
        progress=ProgressPublisher(redis),
        log=structlog.get_logger("test"),
        message_id="1-0",
    )

    ctx = CallContext.from_job(job_ctx)

    assert ctx.org_id == str(envelope.org_id)
    assert ctx.research_job_id == str(envelope.research_job_id)
    assert ctx.trace_id == envelope.trace_id
    assert ctx.cost_cap_micros == 250_000


async def test_an_envelope_without_an_org_cannot_reach_a_connector(
    make_envelope: EnvelopeFactory, redis: Redis
) -> None:
    job_ctx = JobContext(
        envelope=make_envelope("system.ping", org_id=None),
        redis=redis,
        progress=ProgressPublisher(redis),
        log=structlog.get_logger("test"),
        message_id="1-0",
    )
    # Fails fast: a retry cannot conjure an org, and connectors must never run unattributed.
    with pytest.raises(InvalidInputError, match="org context"):
        CallContext.from_job(job_ctx)
