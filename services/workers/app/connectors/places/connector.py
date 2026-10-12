"""Discovery from the local place index (ADR-0016, ADR-0017).

Two connectors, one implementation. Overture is the discovery source; OpenStreetMap is the
second dataset Phase 4 verifies values against. They are separate connectors rather than one
with a parameter because provenance names the dataset a value came from, the planner chooses
between them by cost and coverage, and `sources.key` is per dataset.

What makes these different from every other connector here: there is no network call. The
index is a SQLite file on local disk, so there is no quota, no rate limit, no retry, no
blocked-access detection and nothing to meter -- which is the entire reason it exists, because
storage rather than any API quota is what bounds this product (ADR-0016).

The index is injected at construction, exactly as `PlaceSearchCache` is for Google Places, so
no part of the connector contract has to bend to accommodate a source that reads a file.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import ClassVar

from app.connectors.base import BaseConnector
from app.connectors.places.mapping import released_at_of, to_candidate, to_field_values
from app.connectors.types import (
    AuthKind,
    Candidate,
    ConnectorHealth,
    DiscoveryQuery,
    FieldValue,
    RateLimit,
    RawResult,
    SourceRef,
    TosClass,
)
from app.jobs.errors import InvalidInputError, ParseFailedError
from app.metering.context import CallContext
from app.places.index import PlaceIndex
from app.places.model import OSM, OVERTURE, Place

#: Fields a bulk place dataset can fill. `email` is in the list because Overture carries one for
#: 38% of Indian businesses, which is five times what crawling their websites yields.
FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "category",
        "address",
        "city",
        "state",
        "country",
        "postal_code",
        "phone",
        "website",
        "email",
        "geo",
        "business_status",
        "facebook",
        "instagram",
        "linkedin",
        "x",
        "youtube",
        "whatsapp",
    }
)


class PlaceIndexConnector:
    """Everything a place-index connector does; the datasets differ only in class attributes.

    A mixin rather than a `BaseConnector` subclass. `BaseConnector.__init_subclass__` requires
    `key`, `default_ttl_days` and the rest of the policy declaration from any subclass that is
    not abstract, and this one implements `search`, `fetch` and `map`, so it would count as
    concrete and be asked for a `key` it has no business having. The two real connectors below
    inherit from this and from `BaseConnector`, and the checks run where they should.
    """

    #: Declared, not assigned: the mixin uses the dataset's key to filter the index and to
    #: label provenance, and each connector below supplies it. An annotation alone creates no
    #: attribute, so `BaseConnector`'s subclass check still sees this class as incomplete.
    key: ClassVar[str]

    tos_class: ClassVar[TosClass] = "green"
    auth: ClassVar[AuthKind] = "none"
    fields_provided: ClassVar[frozenset[str]] = FIELDS
    #: A local file answers as fast as SQLite can scan an index, so the only reason to bound
    #: concurrency at all is to leave the event loop's thread pool room for everything else.
    rate_limit: ClassVar[RateLimit] = RateLimit(requests=1000, per_seconds=1.0, concurrency=4)
    cost_per_call_micros: ClassVar[int] = 0

    def __init__(self, index: PlaceIndex) -> None:
        self._index = index

    @property
    def index(self) -> PlaceIndex:
        return self._index

    async def search(self, query: DiscoveryQuery, ctx: CallContext) -> list[Candidate]:
        """Businesses of the requested kind inside the query's box.

        A bounding box is required. Every other source can fall back to interpreting the text,
        but this one matches rows in a spatial index and a query without a box would mean "every
        business in India", which is not a search anyone asked for.
        """
        if query.bbox is None:
            raise InvalidInputError(f"{self.key} needs a bounding box to search in")

        categories = _categories(query)
        # Ask by category where the taxonomy knows one. 24 of our 319 categories have no Overture
        # equivalent (ADR-0017), and for those the fallback is the trade the user named -- never
        # `query.text`, which is a phrase like "gym in Gurgaon" that no business is called. That
        # mistake returned zero candidates from an index holding 553 gyms in the same box.
        name = None if categories else _text(query.options.get("name_hint"))

        places = await asyncio.to_thread(
            self._index.search,
            bbox=query.bbox,
            categories=categories,
            source=self.key,
            name=name,
            contactable=bool(query.options.get("contactable")),
            has_website=_tristate(query.options.get("has_website")),
            requires_contact=_strings(query.options.get("contact_any_of")),
            limit=query.max_results,
        )
        released_at = await self._released_at()
        return [to_candidate(p, released_at=released_at) for p in places]

    async def fetch(self, ref: SourceRef, ctx: CallContext) -> RawResult:
        """One record, as the index holds it.

        The search already carried every field, so nothing in the pipeline needs this. It exists
        because the contract has it and because a value under dispute should be re-readable from
        its own id rather than by running the search again and hoping the same row comes back.
        """
        place = await asyncio.to_thread(self._one, ref.external_id)
        if place is None:
            raise ParseFailedError(f"{self.key} has no record {ref.external_id}")
        body = json.dumps(_as_dict(place), sort_keys=True).encode()
        return RawResult(
            ref=ref,
            status=200,
            body=body,
            content_type="application/json",
            fetched_at=datetime.now(UTC),
            url=place.source_url,
        )

    def map(self, raw: RawResult) -> list[FieldValue]:
        try:
            place = _from_dict(json.loads(raw.body))
        except (ValueError, KeyError, TypeError) as exc:
            raise ParseFailedError(f"{self.key} record is not readable: {exc}") from exc
        return to_field_values(place, released_at=place.updated_at)

    async def health(self) -> ConnectorHealth:
        """Whether the index exists and holds anything.

        An index that is missing or empty is the realistic failure here -- a deployment without
        the volume mounted, or one where the build has not run yet -- and it is silent: searches
        simply return nothing, which reads like an area with no businesses in it.
        """
        checked_at = datetime.now(UTC)
        if not self._index.exists():
            return ConnectorHealth(
                key=self.key,
                ok=False,
                detail=f"no place index at {self._index.path}",
                checked_at=checked_at,
            )
        stats = await asyncio.to_thread(self._index.stats)
        if stats.places == 0:
            return ConnectorHealth(
                key=self.key, ok=False, detail="place index is empty", checked_at=checked_at
            )
        extract = stats.extract_date.date().isoformat() if stats.extract_date else "unknown"
        return ConnectorHealth(
            key=self.key,
            ok=True,
            detail=f"{stats.places} places, data from {extract}",
            checked_at=checked_at,
        )

    async def _released_at(self) -> datetime | None:
        return released_at_of(self.key, (await asyncio.to_thread(self._index.stats)).extract_date)

    def _one(self, external_id: str) -> Place | None:
        # Whole-world box: this is a lookup by id, and the bbox is only there because `search`
        # is the one read the index exposes.
        for place in self._index.search(
            bbox=(-90.0, -180.0, 90.0, 180.0), source=self.key, limit=10_000
        ):
            if place.external_id == external_id:
                return place
        return None


class OvertureConnector(PlaceIndexConnector, BaseConnector):
    """Overture Maps Places: the discovery source.

    Measured on Delhi: 269,309 businesses, 67.1% with their own phone number and 38.0% with
    their own email, against 9.7% and 2.7% for OpenStreetMap on the same box. Free, with no
    quota and nobody able to withdraw it.

    CDLA-Permissive-2.0 (Apache 2.0 for Foursquare-derived rows, CC0 for AllThePlaces). Sharing
    the data obliges us to ship the agreement text with it, so an export built from these values
    carries `docs/licenses/CDLA-Permissive-2.0.md`.
    """

    key: ClassVar[str] = OVERTURE
    #: Overture publishes monthly, so a snapshot is a month old at worst by the time the next
    #: one lands. Values older than that have been superseded by a release nobody ingested.
    default_ttl_days: ClassVar[int] = 35


class OsmConnector(PlaceIndexConnector, BaseConnector):
    """OpenStreetMap: the second dataset, for verifying values rather than finding leads.

    Its contact coverage is too thin to lead with -- 9.7% phone, 2.7% email on Delhi -- but it
    is produced by people who have no relationship with Overture's contributors, which is what
    makes a disagreement between them worth something (ADR-0017, Phase 4).

    ODbL 1.0: "data (c) OpenStreetMap contributors" wherever its values are shown or exported.
    """

    key: ClassVar[str] = OSM
    #: OSM is continuous rather than released, and an element carries its own last-edited date,
    #: so freshness is per record and this is only the ceiling on a whole rebuild.
    default_ttl_days: ClassVar[int] = 35
    #: It can find businesses, but the planner should not choose it to: `providing()` sorts by
    #: cost and both are free, so leaving this true would make the tie-break alphabetical and
    #: put the thinner dataset first.
    discovers: ClassVar[bool] = False


def _categories(query: DiscoveryQuery) -> tuple[str, ...]:
    """The index categories this query asks for.

    The planner puts all of them in `categories` and the first in `category`; Places takes one
    `includedType` per request where the index matches a list in one query, so reading only
    `category` here would lose two thirds of the market for an industry that means three.
    """
    listed = query.options.get("categories")
    if isinstance(listed, list) and listed:
        return tuple(str(c) for c in listed if c)
    return (query.category,) if query.category else ()


def _as_dict(place: Place) -> dict[str, object]:
    return {
        "source": place.source,
        "external_id": place.external_id,
        "name": place.name,
        "category": place.category,
        "categories": list(place.categories),
        "lat": place.lat,
        "lon": place.lon,
        "source_url": place.source_url,
        "phone": place.phone,
        "website": place.website,
        "email": place.email,
        "socials": list(place.socials),
        "street": place.street,
        "housenumber": place.housenumber,
        "city": place.city,
        "postcode": place.postcode,
        "region": place.region,
        "country": place.country,
        "confidence": place.confidence,
        "operating_status": place.operating_status,
        "updated_at": place.updated_at.isoformat() if place.updated_at else None,
    }


def _from_dict(data: dict[str, object]) -> Place:
    """Rebuilds a place from the JSON `fetch` produced.

    Every field is narrowed rather than cast. A stored record is data, and data read back as
    whatever type it happens to hold is how a null becomes the string "None" three layers later.
    """
    return Place(
        source=_require_text(data, "source"),
        external_id=_require_text(data, "external_id"),
        name=_require_text(data, "name"),
        category=_require_text(data, "category"),
        categories=_strings(data.get("categories")),
        lat=_require_number(data, "lat"),
        lon=_require_number(data, "lon"),
        source_url=_text(data.get("source_url")) or "",
        phone=_text(data.get("phone")),
        website=_text(data.get("website")),
        email=_text(data.get("email")),
        socials=_strings(data.get("socials")),
        street=_text(data.get("street")),
        housenumber=_text(data.get("housenumber")),
        city=_text(data.get("city")),
        postcode=_text(data.get("postcode")),
        region=_text(data.get("region")),
        country=_text(data.get("country")),
        confidence=_number(data.get("confidence")),
        operating_status=_text(data.get("operating_status")),
        updated_at=_time(data.get("updated_at")),
    )


def _require_text(data: dict[str, object], key: str) -> str:
    value = _text(data.get(key))
    if value is None:
        raise KeyError(key)
    return value


def _require_number(data: dict[str, object], key: str) -> float:
    value = _number(data.get(key))
    if value is None:
        raise KeyError(key)
    return value


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _tristate(value: object) -> bool | None:
    """A filter that means three things: require it, forbid it, or do not care.

    `bool()` would collapse the middle one into "forbid", which is how "restaurants in Delhi"
    would quietly become "restaurants in Delhi with no website".
    """
    return value if isinstance(value, bool) else None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(v for v in value if isinstance(v, str))


def _time(value: object) -> datetime | None:
    text = _text(value)
    return datetime.fromisoformat(text) if text else None
