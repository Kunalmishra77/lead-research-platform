"""Seeded reference data the workers need: where a place is, what Google calls a trade, and
which source a value came from.

Both tables are part of the global graph (ADR-0007), not tenant data, so these read without a
tenant context. They are read once per process and cached: the seed changes when someone runs
`pnpm db:seed`, never inside a job.

The planner cannot invent either of these. A bounding box is what turns "Pune" into an area the
Places connector can actually tile (docs/06 section 2), and `industries.google_types` is what
turns "dental clinics" into the `includedType` that stops a text search drifting into anything
whose page happens to mention teeth.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.jobs.errors import InvalidInputError

#: Kinds small enough that one search covers them. Tiling within a city is the Places connector's
#: job — it subdivides when a tile returns the page cap — so this only says what is a city at all.
CITY_KINDS: frozenset[str] = frozenset({"city", "locality"})


@dataclass(frozen=True, slots=True)
class GeoArea:
    """A seeded place, with the box that covers it."""

    slug: str
    name: str
    kind: str
    country: str
    bbox: tuple[float, float, float, float]
    population: int | None
    #: Other names the seed knows it by, already slugified.
    aliases: frozenset[str] = frozenset()

    @property
    def is_city(self) -> bool:
        return self.kind in CITY_KINDS

    def answers_to(self, slug: str) -> bool:
        return slug == self.slug or slug in self.aliases


@dataclass(frozen=True, slots=True)
class Source:
    """A row of `app.sources`, which every stored value has to point at.

    `field_values.source_id` is a NOT NULL foreign key, so nothing can be written without one,
    and the uuid is generated at seed time rather than fixed — the stable handle is the `key` a
    connector declares (`BaseConnector.key`).
    """

    id: str
    key: str
    #: When a value from this source must be deleted, where the provider's terms require it
    #: (ADR-0011). Null means ours to keep.
    retention_days: int | None
    default_ttl_days: int


@dataclass(frozen=True, slots=True)
class Industry:
    slug: str
    name: str
    #: Places API (New) types. Empty is normal and means the search runs on its text alone.
    google_types: tuple[str, ...]
    #: Overture Places categories this industry means (ADR-0017). Matched against every level of
    #: a place's category hierarchy, so a request for restaurants reaches the Indian and pizza
    #: ones filed beneath it. Empty where Overture has no equivalent, which is an answer and not
    #: a gap: discovery falls back to searching names inside the area.
    overture_categories: tuple[str, ...]
    synonyms: tuple[str, ...]


_EMPTY_INDUSTRY = Industry(slug="", name="", google_types=(), overture_categories=(), synonyms=())


class ReferenceData:
    """Read-through cache over the two seeded lookup tables."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._areas: tuple[GeoArea, ...] | None = None
        self._industries: dict[str, Industry] | None = None
        #: Name and synonym -> slug, built once from the industries above.
        self._by_term: dict[str, str] | None = None
        self._sources: dict[str, Source] | None = None
        self._rates: dict[str, int] | None = None

    async def areas(self) -> tuple[GeoArea, ...]:
        if self._areas is None:
            self._areas = await self._load_areas()
        return self._areas

    async def industries(self) -> dict[str, Industry]:
        if self._industries is None:
            self._industries = await self._load_industries()
        return self._industries

    async def sources(self) -> dict[str, Source]:
        if self._sources is None:
            self._sources = await self._load_sources()
        return self._sources

    async def credits_per_unit(self, meter: str) -> int:
        """What one delivered unit of this meter costs the customer (`app.credit_rates`).

        Read from the table rather than hardcoded, because the price of a lead is a commercial
        decision that changes without a deploy. A meter with no rate charges nothing: billing for
        something at a price nobody set is worse than not billing for it.
        """
        return await self.credit_rate(meter) or 0

    async def credit_rate(self, meter: str) -> int | None:
        """The rate, or None when nobody has set one for this meter.

        Separate from `credits_per_unit` because zero and unpriced are different answers and the
        caller that charges a lead has to tell them apart: a source priced at zero is free on
        purpose, and a source with no rate is one nobody has thought about yet.
        """
        if self._rates is None:
            self._rates = await self._load_rates()
        return self._rates.get(meter)

    async def source_id(self, key: str) -> str:
        """The uuid to store against a value from this connector.

        Fails loudly rather than inventing one: `field_values.source_id` is a foreign key, so a
        wrong guess is rejected by the database anyway, and a missing row means the seed has not
        been run against this environment — which is worth saying plainly once rather than
        discovering as a constraint violation on every candidate.
        """
        known = await self.sources()
        source = known.get(key)
        if source is None:
            raise InvalidInputError(
                f"no app.sources row for {key!r}; run `pnpm db:seed` (known: {sorted(known)})"
            )
        return source.id

    async def find_industries(self, terms: Sequence[str]) -> tuple[str, ...]:
        """Taxonomy slugs for the words a user used, in order and without duplicates.

        The spec carries `filters.industry.include` as free text -- "gyms", "dental clinic" --
        because that is what a person types and what the parser hears. `taxonomy_ids` is the
        machine answer, and nothing was filling it: the model would have to know 319 slugs by
        heart to do it, and it does not.

        So it is resolved here, deterministically, against the names and synonyms the seed
        carries for exactly this purpose ("a synonym belongs to exactly one category, so query
        expansion stays unambiguous", db/seeds/industries.ts).

        This mattered less than it looks until discovery stopped being a maps API. Google reads
        "gym in Gurgaon" as a phrase and answers sensibly; the place index matches categories and
        names literally, so an unresolved industry meant a search for businesses called "gym in
        Gurgaon", of which there are none. The first live run returned zero candidates from an
        index holding 553 gyms in that very box.

        A term nobody recognises is skipped rather than guessed at. The search still runs on its
        text, which is a worse search but an honest one.
        """
        known = await self.industries()
        if self._by_term is None:
            self._by_term = {
                term: industry.slug
                for industry in known.values()
                for term in {slugify(industry.name), *(slugify(s) for s in industry.synonyms)}
                if term
            }
        found: dict[str, None] = {}
        for term in terms:
            slug = self._by_term.get(slugify(term)) or (
                slugify(term) if slugify(term) in known else None
            )
            if slug:
                found.setdefault(slug, None)
        return tuple(found)

    async def find_area(self, name: str, *, country: str | None = None) -> GeoArea | None:
        """A place by the words the user used, or None when the seed has never heard of it.

        None is a real answer and callers must treat it as one: guessing a bounding box would
        send every search in the job to the wrong part of the world, which is worse than telling
        the user we do not cover that place yet.
        """
        wanted = slugify(name)
        if not wanted:
            return None
        matches = [
            area
            for area in await self.areas()
            if area.answers_to(wanted) and (country is None or area.country == country.upper())
        ]
        if not matches:
            return None
        # A city beats the state of the same name ("Delhi"), and between two cities sharing a
        # name the larger wins: naming no state, a user almost always means that one.
        return max(matches, key=lambda a: (a.is_city, a.population or 0))

    async def google_types_for(self, taxonomy_ids: list[str]) -> tuple[str, ...]:
        """Places types for the spec's industries, in order and without duplicates."""
        return await self._source_categories(taxonomy_ids, "google_types")

    async def overture_categories_for(self, taxonomy_ids: list[str]) -> tuple[str, ...]:
        """Overture categories for the spec's industries, in order and without duplicates.

        Unlike `google_types_for`, all of them are returned rather than the first: Places takes
        one `includedType` per request, while the place index matches a list in one query, and
        an industry that means three Overture categories loses two thirds of its market if only
        the first is used.
        """
        return await self._source_categories(taxonomy_ids, "overture_categories")

    async def _source_categories(self, taxonomy_ids: list[str], attribute: str) -> tuple[str, ...]:
        known = await self.industries()
        seen: dict[str, None] = {}
        for slug in taxonomy_ids:
            industry = known.get(slug, _EMPTY_INDUSTRY)
            for value in getattr(industry, attribute):
                seen.setdefault(value, None)
        return tuple(seen)

    async def _load_areas(self) -> tuple[GeoArea, ...]:
        sql = (
            "select slug, name, kind::text as kind, country, min_lat, min_lng, max_lat, max_lng,"
            " population, aliases from app.geo_areas"
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(text(sql))).mappings().all()
        return tuple(
            GeoArea(
                slug=row["slug"],
                name=row["name"],
                kind=row["kind"],
                country=row["country"],
                bbox=(row["min_lat"], row["min_lng"], row["max_lat"], row["max_lng"]),
                population=row["population"],
                # The seed's slugs are state-qualified ("pune-maharashtra"), so the plain name
                # has to be an alias or nothing a user types would ever match.
                aliases=frozenset(
                    {slugify(alias) for alias in row["aliases"]} | {slugify(row["name"])}
                ),
            )
            for row in rows
        )

    async def _load_rates(self) -> dict[str, int]:
        sql = "select meter, credits_per_unit from app.credit_rates"
        async with self._engine.connect() as conn:
            rows = (await conn.execute(text(sql))).mappings().all()
        return {row["meter"]: int(row["credits_per_unit"]) for row in rows}

    async def _load_sources(self) -> dict[str, Source]:
        sql = "select id, key, retention_days, default_ttl_days from app.sources"
        async with self._engine.connect() as conn:
            rows = (await conn.execute(text(sql))).mappings().all()
        return {
            row["key"]: Source(
                id=str(row["id"]),
                key=row["key"],
                retention_days=row["retention_days"],
                default_ttl_days=row["default_ttl_days"],
            )
            for row in rows
        }

    async def _load_industries(self) -> dict[str, Industry]:
        sql = "select slug, name, google_types, overture_categories, synonyms from app.industries"
        async with self._engine.connect() as conn:
            rows = (await conn.execute(text(sql))).mappings().all()
        return {
            row["slug"]: Industry(
                slug=row["slug"],
                name=row["name"],
                google_types=tuple(row["google_types"]),
                overture_categories=tuple(row["overture_categories"]),
                synonyms=tuple(row["synonyms"]),
            )
            for row in rows
        }


def slugify(value: str) -> str:
    """The seed's slug form: lowercase, alphanumerics, single hyphens."""
    return "-".join("".join(c if c.isalnum() else " " for c in value.casefold()).split())
