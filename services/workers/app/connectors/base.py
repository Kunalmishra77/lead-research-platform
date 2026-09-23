"""The contract every data source implements (docs/08).

A connector owns exactly one source. It declares its policy class and limits as class attributes,
so the planner and the source policy can reason about it without calling it. `map()` is pure and
deterministic, which is what makes recorded fixtures a meaningful test.

Connectors are registered once and shared by every job, so they must hold no per-org state: what
belongs to one job travels in the `ConnectorContext` passed to each call.
"""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import ClassVar

from app.connectors.types import (
    AuthKind,
    Candidate,
    ConnectorContext,
    ConnectorHealth,
    DiscoveryQuery,
    FieldValue,
    RateLimit,
    RawResult,
    SourceRef,
    TosClass,
)


class BaseConnector(ABC):
    #: Matches app.sources.key (seeded), e.g. "google_places".
    key: ClassVar[str]
    tos_class: ClassVar[TosClass]
    auth: ClassVar[AuthKind]
    #: Field catalogue keys this source can fill (ResearchSpec FieldKey).
    fields_provided: ClassVar[frozenset[str]]
    #: How long a value from this source stays fresh.
    default_ttl_days: ClassVar[int]
    rate_limit: ClassVar[RateLimit]
    #: Internal cost of one call, in micros (docs/11 usage_events.cost_micros).
    cost_per_call_micros: ClassVar[int] = 0
    #: Meter name for usage events; defaults to `api_<key>`.
    meter: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "__abstractmethods__", None):
            return
        missing = [
            name
            for name in (
                "key",
                "tos_class",
                "auth",
                "fields_provided",
                "default_ttl_days",
                "rate_limit",
            )
            if not hasattr(cls, name)
        ]
        if missing:
            raise TypeError(f"{cls.__name__} is missing connector attributes: {', '.join(missing)}")
        # Derived from this class's own key: a subclass must not inherit its parent's meter.
        if not cls.__dict__.get("meter"):
            cls.meter = f"api_{cls.key}"

    @abstractmethod
    async def search(self, query: DiscoveryQuery, ctx: ConnectorContext) -> list[Candidate]:
        """Finds candidate businesses for one query. `ctx` attributes every call to its job."""

    @abstractmethod
    async def fetch(self, ref: SourceRef, ctx: ConnectorContext) -> RawResult:
        """Fetches one record's details, unparsed."""

    @abstractmethod
    def map(self, raw: RawResult) -> list[FieldValue]:
        """Pure, deterministic mapping of a raw result to field values with provenance."""

    async def health(self) -> ConnectorHealth:
        """Cheap liveness check; overridden when a source offers a ping endpoint."""
        return ConnectorHealth(key=self.key, ok=True, checked_at=datetime.now(UTC))

    @classmethod
    def usable_in_production(cls, *, legal_approved: bool) -> bool:
        """Red sources stay off until legal review says otherwise (docs/08 source policy)."""
        return cls.tos_class != "red" or legal_approved
