"""Shared connector types (docs/08 connector contract)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

import structlog

from app.jobs.errors import InvalidInputError

if TYPE_CHECKING:  # keeps this module free of Redis and the progress publisher
    from app.jobs.context import JobContext

TosClass = Literal["green", "amber", "red"]
AuthKind = Literal["none", "api_key", "oauth"]
ValueMethod = Literal["api", "crawl", "ai", "user", "provider"]


@dataclass(frozen=True, slots=True)
class RateLimit:
    """Requests per window and how many may be in flight at once, for one connector.

    Quotas are granted per API key, not per hostname, so the budget belongs to the connector.
    A connector that spans several hosts needs its own per-host limits on top.
    """

    requests: int
    per_seconds: float = 1.0
    concurrency: int = 1


@dataclass(frozen=True, slots=True)
class DiscoveryQuery:
    """One search a connector should run (the planner builds these, task 2.10)."""

    text: str
    #: Bounding box to search inside: (min_lat, min_lng, max_lat, max_lng).
    bbox: tuple[float, float, float, float] | None = None
    #: Source-specific category filter, e.g. a Google Places type.
    category: str | None = None
    country: str | None = None
    language: str = "en"
    max_results: int = 20
    #: Extra per-connector options; never user text that belongs in `text`.
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Points at one record in a source, e.g. a Google place id."""

    source_key: str
    external_id: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    """A business a discovery connector found (docs/06 section 3)."""

    source_key: str
    external_id: str
    name: str
    url: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    country: str | None = None
    lat: float | None = None
    lng: float | None = None
    #: Where the value can be seen (stored as provenance `source_url`).
    source_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RawResult:
    """An unparsed source response, kept exactly as received (docs/06 raw_documents)."""

    ref: SourceRef
    status: int
    body: bytes
    content_type: str | None
    fetched_at: datetime
    url: str


@dataclass(frozen=True, slots=True)
class FieldValue:
    """One observed value with its provenance (CLAUDE.md: no value without provenance)."""

    entity_type: Literal["company", "person", "location"]
    field: str
    value: Any
    source_key: str
    source_url: str
    observed_at: datetime
    method: ValueMethod
    confidence: float
    derivation: str | None = None
    model: str | None = None
    prompt_version: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectorHealth:
    key: str
    ok: bool
    detail: str | None = None
    checked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ConnectorContext:
    """What a connector needs from the job it runs for (docs/08).

    Deliberately narrower than `JobContext`: a connector gets attribution and a logger, not the
    Redis handle or the progress publisher, so it cannot reach past its own responsibility.
    """

    org_id: str
    research_job_id: str | None
    trace_id: str
    log: structlog.stdlib.BoundLogger
    #: Remaining internal cost cap in micros (0 = uncapped); the executor enforces it.
    cost_cap_micros: int = 0

    @classmethod
    def from_job(cls, ctx: "JobContext") -> "ConnectorContext":
        if ctx.org_id is None:
            # Retrying cannot conjure an org, so this fails fast instead of looking transient.
            raise InvalidInputError("connectors need an org context; this envelope has none")
        envelope = ctx.envelope
        job_id = None if envelope.research_job_id is None else str(envelope.research_job_id)
        return cls(
            org_id=ctx.org_id,
            research_job_id=job_id,
            trace_id=envelope.trace_id,
            log=ctx.log,
            cost_cap_micros=envelope.budget.cost_cap_micros,
        )
