"""Google Places API (New) — the Phase 2 discovery source (docs/08, ADR-0011).

Two calls: `places:searchText` to find businesses in an area, and Place Details to fill in a
single one. Both go through `ConnectorHttpClient`, so rate limiting, retries, blocked-access
detection and metering are not this module's business.

What is this module's business:

* **Field masks.** Google bills by the fields asked for, so the mask is the price. The search
  mask is the smallest set that makes a candidate resolvable; the details mask adds the rest.
* **Tiling.** A query returns at most 60 results whatever the area, so a city is covered by
  tiles, and a tile that comes back full is split again (`tiling.py`).
* **Place IDs.** They are the one thing Google allows us to keep indefinitely, so a repeat of the
  same search is answered from them instead of paying again.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.connectors.base import BaseConnector
from app.connectors.google_places.cache import CachedSearch, PlaceSearchCache
from app.connectors.google_places.mapping import to_candidate, to_field_values
from app.connectors.google_places.models import ApiError, Place, TextSearchResponse
from app.connectors.google_places.tiling import MAX_SPLIT_DEPTH, Tile, tile_bbox
from app.connectors.http_client import CallCost, ConnectorHttpClient
from app.connectors.types import (
    AuthKind,
    Candidate,
    DiscoveryQuery,
    FieldValue,
    RateLimit,
    RawResult,
    SourceRef,
    TosClass,
)
from app.jobs.errors import BudgetExhaustedError, InvalidInputError, ParseFailedError
from app.metering.context import CallContext

BASE_URL = "https://places.googleapis.com/v1"
SEARCH_URL = f"{BASE_URL}/places:searchText"

#: What a discovery result needs to be resolvable and worth paying for.
#:
#: `websiteUri`, `nationalPhoneNumber`, `rating` and `userRatingCount` are **Enterprise** fields
#: (verified against Google's field-to-SKU table on 2026-09-23), and a request is billed at the
#: highest tier it asks for, so this mask costs $35/1000 rather than Pro's $32. It is kept that
#: way deliberately: knowing at search time whether a business has a website is what lets the
#: planner skip a $20/1000 details call for every candidate it would otherwise have to check.
SEARCH_FIELDS = (
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.addressComponents",
    "places.location",
    "places.types",
    "places.primaryType",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.rating",
    "places.userRatingCount",
    "places.businessStatus",
    "places.googleMapsUri",
    "nextPageToken",
)

#: Details adds what a search does not carry and is only worth fetching for a kept candidate.
DETAILS_FIELDS = (
    "id",
    "displayName",
    "formattedAddress",
    "addressComponents",
    "location",
    "types",
    "primaryType",
    "primaryTypeDisplayName",
    "nationalPhoneNumber",
    "internationalPhoneNumber",
    "websiteUri",
    "rating",
    "userRatingCount",
    "businessStatus",
    "googleMapsUri",
    "regularOpeningHours",
)

#: The API's own maximum; asking for more is rejected.
MAX_PAGE_SIZE = 20

#: Text Search Enterprise, $35 per 1000 calls; Place Details Enterprise, $20 per 1000. Both
#: masks reach the Enterprise tier (see SEARCH_FIELDS). Verified 2026-09-23; the free allowance
#: at this tier is 1,000 calls a month, not the 5,000 the Pro SKUs get.
SEARCH_COST_MICROS = 35_000
DETAILS_COST_MICROS = 20_000

#: Fields that put a call into the Enterprise tier. Pinned so a mask change is a priced decision.
ENTERPRISE_FIELDS = frozenset(
    {
        "nationalPhoneNumber",
        "internationalPhoneNumber",
        "websiteUri",
        "rating",
        "userRatingCount",
        "regularOpeningHours",
    }
)

#: Google's own ceiling is 60 results over 3 pages of 20; our own guard, not their convention.
MAX_PAGES = 3

#: However many tiles an area needs, one query may not turn into an unbounded bill.
MAX_TILES_PER_QUERY = 40


class GooglePlacesConnector(BaseConnector):
    key: ClassVar[str] = "google_places"
    tos_class: ClassVar[TosClass] = "green"
    auth: ClassVar[AuthKind] = "api_key"
    fields_provided: ClassVar[frozenset[str]] = frozenset(
        {
            "name",
            "category",
            "address",
            "city",
            "state",
            "country",
            "postal_code",
            "geo",
            "phone",
            "website",
            "google_maps_url",
            "rating",
            "review_count",
            "business_status",
            "opening_hours",
        }
    )
    #: Places content may be kept for 30 days and no longer (ADR-0011). Place IDs are exempt and
    #: are held separately, in the cache and on the source link.
    default_ttl_days: ClassVar[int] = 30
    #: Conservative: Google's per-project quota is not published, so this stays well under any
    #: documented ceiling until the real number is read from the Cloud Console (README).
    rate_limit: ClassVar[RateLimit] = RateLimit(requests=10, per_seconds=1.0, concurrency=4)
    cost_per_call_micros: ClassVar[int] = SEARCH_COST_MICROS

    def __init__(
        self,
        client: ConnectorHttpClient,
        api_key: str,
        *,
        cache: PlaceSearchCache | None = None,
        max_tile_km: float = 15.0,
    ) -> None:
        if not api_key:
            raise InvalidInputError("google_places needs an API key")
        self._client = client
        self._api_key = api_key
        self._cache = cache
        self._max_tile_km = max_tile_km

    async def search(self, query: DiscoveryQuery, ctx: CallContext) -> list[Candidate]:
        """Covers the query's area with tiles and returns every distinct business found."""
        tiles = self._tiles_for(query)
        budget = _Budget(ctx.cost_cap_micros)
        found: dict[str, Candidate] = {}
        for tile in tiles[:MAX_TILES_PER_QUERY]:
            await self._search_tile(query, tile, ctx, found, budget, depth=0)
            if len(found) >= query.max_results:
                break
        return list(found.values())[: query.max_results]

    async def fetch(self, ref: SourceRef, ctx: CallContext) -> RawResult:
        """One place in full. Only worth calling for a candidate we intend to keep."""
        url = f"{BASE_URL}/places/{ref.external_id}"
        response = await self._client.get(
            url,
            cost=CallCost(meter=self.meter, cost_micros=DETAILS_COST_MICROS),
            ctx=ctx,
            headers=self._headers(",".join(DETAILS_FIELDS)),
        )
        _raise_for_api_error(response.status_code, response.content)
        return RawResult(
            ref=ref,
            status=response.status_code,
            body=response.content,
            content_type=response.headers.get("content-type"),
            fetched_at=datetime.now(UTC),
            url=url,
        )

    def map(self, raw: RawResult) -> list[FieldValue]:
        try:
            place = Place.model_validate_json(raw.body)
        except ValueError as exc:
            raise ParseFailedError(f"google_places returned an unreadable place: {exc}") from exc
        return to_field_values(place, raw)

    # ---------------------------------------------------------------- internals

    def _tiles_for(self, query: DiscoveryQuery) -> list[Tile]:
        """A bounded area becomes tiles; an unbounded one is a single text query."""
        if query.bbox is None:
            return [Tile(0.0, 0.0, 0.0, 0.0)]
        tiles = tile_bbox(query.bbox, self._max_tile_km)
        if not tiles:
            # Returning nothing here would be indistinguishable from "no businesses here".
            raise InvalidInputError(f"google_places got an unusable bounding box: {query.bbox}")
        return tiles

    async def _search_tile(
        self,
        query: DiscoveryQuery,
        tile: Tile,
        ctx: CallContext,
        found: dict[str, Candidate],
        budget: "_Budget",
        *,
        depth: int,
    ) -> None:
        """Searches one tile, then splits it when the answer looks cut off at the ceiling."""
        page = await self._search_pages(query, tile, ctx, budget)
        for candidate in page.candidates:
            found.setdefault(candidate.external_id, candidate)

        if not page.truncated or len(found) >= query.max_results:
            return
        # Google stopped at its ceiling, so the rest of this tile was never shown. Halving the
        # area and asking again is the only way to see them.
        if depth >= MAX_SPLIT_DEPTH:
            ctx.log.warning(
                "google_places coverage is incomplete",
                tile=[tile.min_lat, tile.min_lng, tile.max_lat, tile.max_lng],
                term=query.text,
                depth=depth,
                error_class="parse_failed",
            )
            return
        for quarter in tile.quarters():
            await self._search_tile(query, quarter, ctx, found, budget, depth=depth + 1)

    async def _search_pages(
        self, query: DiscoveryQuery, tile: Tile, ctx: CallContext, budget: "_Budget"
    ) -> "_TileResult":
        """One tile, following `nextPageToken` until the API stops or we have enough."""
        cached = await self._cache_get(query, tile)
        if cached is not None:
            return cached

        candidates: list[Candidate] = []
        page_token: str | None = None
        pages = 0
        more_to_come = False
        while pages < MAX_PAGES:
            budget.spend(SEARCH_COST_MICROS)
            body = self._search_body(query, tile, page_token)
            response = await self._client.post(
                SEARCH_URL,
                cost=CallCost(meter=self.meter, cost_micros=SEARCH_COST_MICROS),
                ctx=ctx,
                headers=self._headers(",".join(SEARCH_FIELDS)),
                json=body,
            )
            _raise_for_api_error(response.status_code, response.content)
            try:
                page = TextSearchResponse.model_validate_json(response.content)
            except ValueError as exc:
                raise ParseFailedError(f"google_places search was unreadable: {exc}") from exc

            observed = datetime.now(UTC)
            candidates.extend(
                c for c in (to_candidate(p, observed) for p in page.places) if c is not None
            )
            pages += 1
            page_token = page.next_page_token
            more_to_come = bool(page_token)
            if not page_token or len(candidates) >= query.max_results:
                break

        # Truncated means Google still had more and stopped, not that we had enough: the second
        # is the caller's own limit and says nothing about coverage.
        result = _TileResult(candidates=candidates, truncated=more_to_come and pages >= MAX_PAGES)
        await self._cache_put(query, tile, result)
        return result

    def _search_body(
        self, query: DiscoveryQuery, tile: Tile, page_token: str | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "textQuery": query.text,
            "pageSize": min(MAX_PAGE_SIZE, max(1, query.max_results)),
            "languageCode": query.language,
        }
        if query.country:
            body["regionCode"] = query.country
        if query.category:
            body["includedType"] = query.category
        if tile.span_lat > 0 and tile.span_lng > 0:
            body["locationRestriction"] = {"rectangle": tile.as_rectangle()}
        if page_token:
            body["pageToken"] = page_token
        return body

    def _headers(self, field_mask: str) -> dict[str, str]:
        return {"X-Goog-Api-Key": self._api_key, "X-Goog-FieldMask": field_mask}

    async def _cache_get(self, query: DiscoveryQuery, tile: Tile) -> "_TileResult | None":
        """A repeated search, answered without paying for it again.

        Redis holds the whole candidate, not just the place id, because an id alone is not a
        result: rehydrating one costs a details call, which is dearer than the search we were
        trying to avoid. The entry expires after 30 days, which is both the limit Google's terms
        set for this content and a sweeper we do not have to write (ADR-0011).
        """
        if self._cache is None:
            return None
        cached = await self._cache.get(self.key, _cache_fingerprint(query), tile)
        if cached is None:
            return None
        return _TileResult(candidates=cached.candidates, truncated=cached.truncated)

    async def _cache_put(self, query: DiscoveryQuery, tile: Tile, result: "_TileResult") -> None:
        if self._cache is None or not result.candidates:
            return
        await self._cache.put(
            self.key,
            _cache_fingerprint(query),
            tile,
            CachedSearch(candidates=result.candidates, truncated=result.truncated),
        )


class PlaceNotFoundError(InvalidInputError):
    """A place id Google no longer knows.

    Classified `invalid_input` because retrying sends the same dead id, but given its own type
    so a caller can drop the candidate (or refresh the id) instead of failing the task.
    """


@dataclass(frozen=True, slots=True)
class _TileResult:
    """What one tile produced, and whether Google had more it would not show."""

    candidates: list[Candidate]
    truncated: bool


class _Budget:
    """Stops a tiled search before it spends more than the job allows.

    Bounds one search within one process. The job-wide ceiling is the executor's (task 2.11);
    this is what keeps a single query over a large city from becoming an unbounded bill.
    """

    def __init__(self, cap_micros: int) -> None:
        self._cap = cap_micros
        self._spent = 0

    def spend(self, micros: int) -> None:
        if self._cap > 0 and self._spent + micros > self._cap:
            raise BudgetExhaustedError(
                f"google_places would spend {self._spent + micros} of {self._cap} micros"
            )
        self._spent += micros

    @property
    def spent(self) -> int:
        return self._spent


def _cache_fingerprint(query: DiscoveryQuery) -> str:
    """Everything that changes the answer, so two different questions never share an entry."""
    return "\x1f".join(
        (
            query.text.strip().casefold(),
            query.category or "",
            (query.country or "").upper(),
            query.language,
        )
    )


def _raise_for_api_error(status: int, body: bytes) -> None:
    """Turns Google's error envelope into a classified failure.

    A 4xx that is not a block reaches here because the HTTP client hands non-2xx responses to the
    connector: only this module knows that `INVALID_ARGUMENT` means our field mask is wrong and
    no amount of retrying will help.
    """
    if status < 400:
        return
    try:
        parsed = json.loads(body)
        error = ApiError.from_body(parsed) if isinstance(parsed, dict) else None
    except ValueError:
        error = None
    detail = f"{error.status}: {error.message}" if error else f"http {status}"
    if status == 404:
        # A place id that has gone away. Expected over time, and Google's own advice is to
        # refresh rather than to treat it as a broken request.
        raise PlaceNotFoundError(f"google_places no longer knows this place ({detail})")
    raise InvalidInputError(f"google_places rejected the request ({detail})")
