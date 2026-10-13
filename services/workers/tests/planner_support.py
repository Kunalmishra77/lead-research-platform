"""Fixtures for the planner tests: a seeded geography and a registry, without a database."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import fakeredis
from leadforge_contracts.research_spec import ResearchSpec

from app.connectors.factory import build_connectors
from app.connectors.registry import ConnectorRegistry
from app.db.reference import GeoArea, Industry, slugify
from tests.ai_support import settings

PUNE = GeoArea(
    slug="pune-maharashtra",
    name="Pune",
    kind="city",
    country="IN",
    bbox=(18.4088, 73.6807, 18.6298, 73.9986),
    population=3_124_458,
    aliases=frozenset({"pune", "poona"}),
)
MAHARASHTRA = GeoArea(
    slug="maharashtra",
    name="Maharashtra",
    kind="state",
    country="IN",
    bbox=(15.6, 72.6, 22.0, 80.9),
    population=112_374_333,
    #: `MH` because that is the shape 870 of the live graph's state values have: sources report
    #: the ISO 3166-2 subdivision code, not the name.
    aliases=frozenset({"maharashtra", "mh"}),
)
MUMBAI = GeoArea(
    slug="mumbai-maharashtra",
    name="Mumbai",
    kind="city",
    country="IN",
    bbox=(18.89, 72.77, 19.27, 72.98),
    population=12_442_373,
    aliases=frozenset({"mumbai", "bombay"}),
)
NAGPUR = GeoArea(
    slug="nagpur-maharashtra",
    name="Nagpur",
    kind="city",
    country="IN",
    bbox=(21.03, 78.95, 21.24, 79.18),
    population=2_405_665,
    aliases=frozenset({"nagpur"}),
)
INDIA = GeoArea(
    slug="india",
    name="India",
    kind="country",
    country="IN",
    bbox=(6.7, 68.1, 35.5, 97.4),
    population=1_400_000_000,
    aliases=frozenset({"india", "in"}),
)


class FakeReference:
    """The seeded lookups, in memory. Same answers, no connection."""

    def __init__(
        self, areas: tuple[GeoArea, ...] = (PUNE, MUMBAI, NAGPUR, MAHARASHTRA, INDIA)
    ) -> None:
        self._areas = areas
        self._industries = {
            "dental-clinics": Industry(
                slug="dental-clinics",
                name="Dental clinics",
                google_types=("dentist", "dental_clinic"),
                overture_categories=("dental_clinic",),
                synonyms=("dentist",),
            )
        }

    async def areas(self) -> tuple[GeoArea, ...]:
        return self._areas

    async def industries(self) -> dict[str, Industry]:
        return self._industries

    async def find_industries(self, terms: Sequence[str]) -> tuple[str, ...]:
        """Name and synonym -> slug, as the real one does.

        Spelled out here rather than stubbed to `()`: the planner falls back to this whenever
        the parser leaves `taxonomy_ids` empty, which is every real request, so a fake that
        always answers nothing would test the path nobody takes.
        """
        by_term = {
            term: industry.slug
            for industry in self._industries.values()
            for term in {slugify(industry.name), *(slugify(s) for s in industry.synonyms)}
            if term
        }
        found: dict[str, None] = {}
        for term in terms:
            slug = by_term.get(slugify(term))
            if slug:
                found.setdefault(slug, None)
        return tuple(found)

    async def find_state(self, name: str, *, country: str | None = None) -> GeoArea | None:
        """Only state-kind areas, as the real one does.

        Written out rather than delegated to `find_area`, because the difference between them is
        the thing being tested: `find_area` prefers a city on a tie, and "Delhi" is both.
        """
        wanted = slugify(name)
        matches = [
            a
            for a in self._areas
            if a.kind == "state"
            and a.answers_to(wanted)
            and (country is None or a.country == country.upper())
        ]
        return max(matches, key=lambda a: a.population or 0) if matches else None

    async def find_area(self, name: str, *, country: str | None = None) -> GeoArea | None:
        # The real `slugify`, not a copy of it: a fake that reimplements the thing under test
        # agrees with itself for ever and never notices the original drifting.
        wanted = slugify(name)
        matches = [
            a
            for a in self._areas
            if a.answers_to(wanted) and (country is None or a.country == country.upper())
        ]
        if not matches:
            return None
        return max(matches, key=lambda a: (a.is_city, a.population or 0))

    async def overture_categories_for(self, taxonomy_ids: list[str]) -> tuple[str, ...]:
        """The place index's own category names, which is the path discovery actually takes.

        Absent from this fake until now, so every planner test resolved categories through
        `google_types_for` -- the branch for a source that has not been the discovery source
        since ADR-0017.
        """
        seen: dict[str, None] = {}
        for slug in taxonomy_ids:
            industry = self._industries.get(slug)
            for category in industry.overture_categories if industry else ():
                seen.setdefault(category, None)
        return tuple(seen)

    async def google_types_for(self, taxonomy_ids: list[str]) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for slug in taxonomy_ids:
            industry = self._industries.get(slug)
            for google_type in industry.google_types if industry else ():
                seen.setdefault(google_type, None)
        return tuple(seen)


def make_registry(
    *, places: bool = True, serp: bool = True, index_path: Path | None = None
) -> ConnectorRegistry:
    """A registry with the Phase 2 connectors, built the way the worker builds it.

    `index_path` registers the place index connectors, which is how the real worker gets its
    discovery source (ADR-0017). Without it the registry has only the API-backed sources, and a
    plan built from it chooses one of those -- which is why this argument exists: the end-to-end
    test has to reach the connector discovery actually uses.
    """
    redis = fakeredis.FakeAsyncRedis()
    registry, _ = build_connectors(
        settings=settings(
            GOOGLE_PLACES_API_KEY="places-test-key" if places else None,
            GOOGLE_PLACES_ENABLED=places,
            SERPER_API_KEY="serper-test-key" if serp else None,
            PLACES_INDEX_PATH=index_path,
        ),
        redis=redis,
    )
    return registry


def make_spec(
    *,
    cities: list[str] | None = None,
    states: list[str] | None = None,
    country: str = "IN",
    taxonomy_ids: list[str] | None = None,
    fields: list[str] | None = None,
    intent: str = "prospecting",
    depth: str = "standard",
    max_results: int = 500,
    max_credits: int = 1000,
    has_website: bool | None = None,
    contact_any_of: list[str] | None = None,
    social_required: list[str] | None = None,
) -> ResearchSpec:
    """A valid ResearchSpec, so the tests exercise the contract rather than a convenient dict."""
    filters: dict[str, Any] = {
        "location": {"country": country, "cities": cities or [], "states": states or []}
    }
    if taxonomy_ids:
        filters["industry"] = {"include": ["dental clinics"], "taxonomy_ids": taxonomy_ids}
    else:
        filters["industry"] = {"include": ["dental clinics"]}
    if has_website is not None:
        filters["has_website"] = has_website
    if contact_any_of:
        filters["contact"] = {"any_of": contact_any_of}
    if social_required:
        filters["social"] = {name: {"required": True} for name in social_required}
    return ResearchSpec.model_validate(
        {
            "spec_version": 1,
            "entity": "company",
            "intent": intent,
            "filters": filters,
            "fields": fields or ["name", "website"],
            "depth": depth,
            "limits": {"max_results": max_results, "max_credits": max_credits},
        }
    )
