"""The Google Places connector (docs/08, ADR-0011).

Every response is replayed from `tests/fixtures/google_places/`; the live API is never called.
The fixtures carry the real response shape with invented values, because committing real Places
content would itself be storage beyond the 30 days Google permits.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import fakeredis
import httpx
import pytest
import respx

from app.connectors.factory import build_connectors
from app.connectors.google_places import (
    GooglePlacesConnector,
    PlaceSearchCache,
    Tile,
    tile_bbox,
)
from app.connectors.google_places.cache import (
    PLACES_CONTENT_TTL_S,
    SCHEMA_VERSION,
    CachedSearch,
    cache_key,
)
from app.connectors.google_places.connector import (
    DETAILS_COST_MICROS,
    DETAILS_FIELDS,
    ENTERPRISE_FIELDS,
    MAX_PAGES,
    SEARCH_COST_MICROS,
    SEARCH_FIELDS,
    SEARCH_URL,
    PlaceNotFoundError,
)
from app.connectors.google_places.mapping import to_candidate, to_field_values
from app.connectors.google_places.models import Place
from app.connectors.google_places.tiling import MAX_RESULTS_PER_QUERY
from app.connectors.http_client import ConnectorHttpClient
from app.connectors.types import DiscoveryQuery, RateLimit, RawResult, SourceRef
from app.jobs.errors import (
    BudgetExhaustedError,
    InvalidInputError,
    ParseFailedError,
    RateLimitedError,
)
from tests.ai_support import settings
from tests.connector_support import USER_AGENT, RecordingUsage, make_ctx
from tests.fixtures import load_json

API_KEY = "AIza-test-not-a-real-key"
DETAILS_URL = "https://places.googleapis.com/v1/places/ChIJexampleAAAAAAAAAAAAAAAA1"

#: Pune, roughly. Small enough to be one tile at the default size.
SMALL_BBOX = (18.50, 73.80, 18.56, 73.88)


def make_connector(
    usage: RecordingUsage | None = None, **kwargs: Any
) -> tuple[GooglePlacesConnector, RecordingUsage]:
    recorder = usage or RecordingUsage()
    client = ConnectorHttpClient(
        source_key="google_places",
        rate_limit=RateLimit(requests=100, per_seconds=1.0, concurrency=4),
        user_agent=USER_AGENT,
        usage=recorder,
        client=httpx.AsyncClient(timeout=5.0, headers={"user-agent": USER_AGENT}),
    )
    return GooglePlacesConnector(client, API_KEY, **kwargs), recorder


def search_route(*cases: str) -> Callable[[], list[httpx.Request]]:
    """Answers `places:searchText` with each fixture in turn."""
    route = respx.post(SEARCH_URL).mock(
        side_effect=[httpx.Response(200, json=load_json("google_places", case)) for case in cases]
    )
    return lambda: [call.request for call in route.calls]


@respx.mock
async def test_a_search_returns_candidates_with_a_place_id_and_a_maps_link() -> None:
    sent = search_route("search_success")
    connector, _ = make_connector()
    try:
        candidates = await connector.search(
            DiscoveryQuery(text="dental clinics", bbox=SMALL_BBOX, country="IN", max_results=10),
            make_ctx(),
        )
    finally:
        await connector._client.aclose()

    assert [c.name for c in candidates] == [
        "Example Dental Studio",
        "Second Example Dental Care",
        "Third Example Orthodontics",
    ]
    first = candidates[0]
    assert first.external_id == "ChIJexampleAAAAAAAAAAAAAAAA1"
    assert first.url == "https://example-dental.test"
    assert first.city == "Pune"
    assert first.country == "IN"
    assert first.lat == pytest.approx(18.5158)
    # Provenance has to point somewhere a human can look (docs/06).
    assert first.source_url.startswith("https://maps.google.com/")

    body = json.loads(sent()[0].content)
    assert body["textQuery"] == "dental clinics"
    assert body["regionCode"] == "IN"
    assert "rectangle" in body["locationRestriction"]


@respx.mock
async def test_the_field_mask_and_key_travel_in_headers_not_the_body() -> None:
    sent = search_route("search_success")
    connector, _ = make_connector()
    try:
        await connector.search(DiscoveryQuery(text="cafes", bbox=SMALL_BBOX), make_ctx())
    finally:
        await connector._client.aclose()

    request = sent()[0]
    assert request.headers["x-goog-api-key"] == API_KEY
    mask = request.headers["x-goog-fieldmask"]
    # The mask is the price at this API, so it is asserted rather than assumed.
    assert "places.id" in mask
    assert "places.websiteUri" in mask
    assert "places.reviews" not in mask
    assert API_KEY not in request.content.decode()


@respx.mock
async def test_an_empty_area_yields_nothing_and_is_not_an_error() -> None:
    search_route("search_empty")
    connector, _ = make_connector()
    try:
        candidates = await connector.search(
            DiscoveryQuery(text="submarine dealers", bbox=SMALL_BBOX), make_ctx()
        )
    finally:
        await connector._client.aclose()

    assert candidates == []


@respx.mock
async def test_pages_are_followed_until_the_results_are_enough() -> None:
    sent = search_route("search_paged_one", "search_page_two")
    connector, _ = make_connector()
    try:
        candidates = await connector.search(
            DiscoveryQuery(text="dental clinics", bbox=SMALL_BBOX, max_results=10), make_ctx()
        )
    finally:
        await connector._client.aclose()

    assert len(candidates) == 4
    # The second call carries the token from the first, or the fourth result is never seen.
    assert json.loads(sent()[1].content)["pageToken"] == "EXAMPLE_NEXT_PAGE_TOKEN"


@respx.mock
async def test_a_paid_search_is_metered_against_the_job() -> None:
    search_route("search_success")
    connector, usage = make_connector()
    try:
        await connector.search(DiscoveryQuery(text="cafes", bbox=SMALL_BBOX), make_ctx())
    finally:
        await connector._client.aclose()

    assert len(usage.calls) == 1
    assert usage.calls[0]["meter"] == "api_google_places"
    # Enterprise tier: the mask asks for a website and a phone number (README).
    assert usage.calls[0]["cost_micros"] == SEARCH_COST_MICROS == 35_000


@respx.mock
async def test_a_rejected_request_says_what_google_said() -> None:
    respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(400, json=load_json("google_places", "invalid_field_mask"))
    )
    connector, _ = make_connector()
    try:
        with pytest.raises(InvalidInputError) as err:
            await connector.search(DiscoveryQuery(text="cafes", bbox=SMALL_BBOX), make_ctx())
    finally:
        await connector._client.aclose()

    # A bad field mask is our bug; retrying would send the same broken request.
    assert "INVALID_ARGUMENT" in str(err.value)


@respx.mock
async def test_a_quota_failure_is_a_rate_limit_not_a_bug() -> None:
    respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(429, json=load_json("google_places", "rate_limited"))
    )
    connector, _ = make_connector()
    try:
        with pytest.raises(RateLimitedError):
            await connector.search(DiscoveryQuery(text="cafes", bbox=SMALL_BBOX), make_ctx())
    finally:
        await connector._client.aclose()


@respx.mock
async def test_a_disabled_api_reads_as_restricted_access() -> None:
    respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(403, json=load_json("google_places", "permission_denied"))
    )
    connector, _ = make_connector()
    try:
        # 403 is a block by the shared client's rules; the connector never sees it, and nothing
        # here may try to work around it (docs/08).
        with pytest.raises(Exception, match="http_403"):
            await connector.search(DiscoveryQuery(text="cafes", bbox=SMALL_BBOX), make_ctx())
    finally:
        await connector._client.aclose()


@respx.mock
async def test_details_fill_in_what_a_search_does_not_carry() -> None:
    route = respx.get(DETAILS_URL).mock(
        return_value=httpx.Response(200, json=load_json("google_places", "details_success"))
    )
    connector, usage = make_connector()
    try:
        raw = await connector.fetch(
            SourceRef(source_key="google_places", external_id="ChIJexampleAAAAAAAAAAAAAAAA1"),
            make_ctx(),
        )
    finally:
        await connector._client.aclose()

    assert raw.status == 200
    assert raw.fetched_at.tzinfo is not None
    assert usage.calls[0]["cost_micros"] == DETAILS_COST_MICROS
    assert "regularOpeningHours" in route.calls.last.request.headers["x-goog-fieldmask"]


def test_every_mapped_value_carries_provenance_and_the_time_it_was_seen() -> None:
    observed = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    place = Place.model_validate(load_json("google_places", "details_success"))
    raw = RawResult(
        ref=SourceRef(source_key="google_places", external_id=place.id),
        status=200,
        body=b"{}",
        content_type="application/json",
        fetched_at=observed,
        url="https://places.googleapis.com/v1/places/x",
    )

    values = to_field_values(place, raw.fetched_at)
    by_field = {v.field: v for v in values}

    assert by_field["name"].value == "Example Dental Studio"
    assert by_field["phone"].value == "+91 20 2555 0111"
    assert by_field["website"].value == "https://example-dental.test"
    assert by_field["city"].value == "Pune"
    assert by_field["state"].value == "Maharashtra"
    assert by_field["country"].value == "IN"
    assert by_field["postal_code"].value == "411001"
    assert by_field["geo"].value == {"lat": 18.5158, "lng": 73.8418}
    assert by_field["business_status"].value == "operational"
    assert by_field["category"].value == "Dental Clinic"

    for value in values:
        assert value.source_key == "google_places"
        assert value.source_url.startswith("https://")
        assert value.method == "api"
        # The sweeper deletes on observed_at + 30 days, so it must be when we fetched.
        assert value.observed_at == observed


def test_opening_hours_keep_the_week_and_drop_the_moment() -> None:
    place = Place.model_validate(load_json("google_places", "details_success"))
    raw = RawResult(
        ref=SourceRef(source_key="google_places", external_id=place.id),
        status=200,
        body=b"{}",
        content_type=None,
        fetched_at=datetime.now(UTC),
        url="https://places.googleapis.com/v1/places/x",
    )

    hours = next(
        v for v in to_field_values(place, raw.fetched_at) if v.field == "opening_hours"
    ).value
    assert hours[0] == {"day": 1, "open": "09:30", "close": "19:00"}
    # `openNow` is true only at the instant of the call, so it is not stored as a fact.
    assert all("open_now" not in entry for entry in hours)


def test_a_place_without_a_field_produces_no_value_for_it() -> None:
    body = load_json("google_places", "search_success")["places"][2]
    place = Place.model_validate(body)
    raw = RawResult(
        ref=SourceRef(source_key="google_places", external_id=place.id),
        status=200,
        body=b"{}",
        content_type=None,
        fetched_at=datetime.now(UTC),
        url="https://places.googleapis.com/v1/places/x",
    )

    fields = {v.field for v in to_field_values(place, raw.fetched_at)}
    # This place has no phone and no rating; inventing an empty one would look like a fact.
    assert "phone" not in fields
    assert "rating" not in fields
    assert "website" in fields


def test_unreadable_json_is_a_parse_failure_not_a_crash() -> None:
    connector, _ = make_connector()
    raw = RawResult(
        ref=SourceRef(source_key="google_places", external_id="x"),
        status=200,
        body=b"{not json",
        content_type="application/json",
        fetched_at=datetime.now(UTC),
        url="https://places.googleapis.com/v1/places/x",
    )

    with pytest.raises(ParseFailedError):
        connector.map(raw)


def test_a_connector_without_a_key_refuses_to_exist() -> None:
    client = ConnectorHttpClient(
        source_key="google_places",
        rate_limit=RateLimit(requests=1),
        user_agent=USER_AGENT,
        client=httpx.AsyncClient(),
    )
    with pytest.raises(InvalidInputError, match="needs an API key"):
        GooglePlacesConnector(client, "")


# ---------------------------------------------------------------- tiling


def test_a_small_area_is_one_tile_and_a_large_one_is_many() -> None:
    assert len(tile_bbox(SMALL_BBOX, max_km=15.0)) == 1
    # Delhi's bounding box at 15 km tiles: far more than one search can return.
    delhi = tile_bbox((28.40, 76.84, 28.88, 77.35), max_km=15.0)
    assert len(delhi) > 4
    assert all(t.width_km() <= 15.5 and t.height_km() <= 15.5 for t in delhi)


def test_tiles_cover_the_whole_box_without_gaps() -> None:
    bbox = (18.0, 73.0, 19.0, 74.0)
    tiles = tile_bbox(bbox, max_km=25.0)

    assert min(t.min_lat for t in tiles) == pytest.approx(bbox[0])
    assert min(t.min_lng for t in tiles) == pytest.approx(bbox[1])
    assert max(t.max_lat for t in tiles) == pytest.approx(bbox[2])
    assert max(t.max_lng for t in tiles) == pytest.approx(bbox[3])
    covered = sum(t.span_lat * t.span_lng for t in tiles)
    assert covered == pytest.approx((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))


def test_an_inverted_or_empty_box_is_no_tiles() -> None:
    assert tile_bbox((19.0, 74.0, 18.0, 73.0)) == []
    assert tile_bbox((18.0, 73.0, 18.0, 73.0)) == []


def test_the_result_ceiling_is_three_pages_of_twenty() -> None:
    # 60 results over 3 pages is the API's own maximum; ours must not exceed it.
    assert MAX_PAGES * 20 == MAX_RESULTS_PER_QUERY


def test_a_tile_splits_into_four_until_it_is_pointless() -> None:
    quarters = Tile(18.0, 73.0, 19.0, 74.0).quarters()
    assert len(quarters) == 4
    assert sum(q.span_lat * q.span_lng for q in quarters) == pytest.approx(1.0)
    # Below a city block, splitting again would just buy more searches.
    assert Tile(18.0, 73.0, 18.001, 73.001).quarters() == []


def _paged(places: list[dict[str, Any]], token: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"places": places}
    if token:
        body["nextPageToken"] = token
    return body


@respx.mock
async def test_a_tile_google_truncated_is_split_and_searched_again() -> None:
    twenty = load_json("google_places", "search_success")["places"] * 7
    # Three full pages that still offer a fourth: Google had more and stopped.
    full = [httpx.Response(200, json=_paged(twenty, "MORE")) for _ in range(MAX_PAGES)]
    empty = [
        httpx.Response(200, json=load_json("google_places", "search_empty")) for _ in range(12)
    ]
    route = respx.post(SEARCH_URL).mock(side_effect=full + empty)
    connector, _ = make_connector()
    try:
        await connector.search(
            DiscoveryQuery(text="cafes", bbox=SMALL_BBOX, max_results=500), make_ctx()
        )
    finally:
        await connector._client.aclose()

    # Three pages for the parent tile, then one for each of its four quarters.
    assert route.call_count == MAX_PAGES + 4


@respx.mock
async def test_our_own_result_limit_is_not_mistaken_for_google_running_out() -> None:
    twenty = load_json("google_places", "search_success")["places"] * 7
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_paged(twenty, "MORE"))
    )
    connector, _ = make_connector()
    try:
        # The caller wants 5. Stopping early is our decision and says nothing about coverage,
        # so the tile must not be split as though results were hidden from us.
        await connector.search(
            DiscoveryQuery(text="cafes", bbox=SMALL_BBOX, max_results=5), make_ctx()
        )
    finally:
        await connector._client.aclose()

    assert route.call_count == 1


@respx.mock
async def test_a_tiled_search_stops_at_the_jobs_cost_cap_and_keeps_what_it_bought() -> None:
    twenty = load_json("google_places", "search_success")["places"] * 7
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=_paged(twenty, "MORE")))
    connector, usage = make_connector()
    try:
        # Two searches' worth of budget: the third would be over the cap.
        found = await connector.search(
            DiscoveryQuery(text="cafes", bbox=SMALL_BBOX, max_results=500),
            make_ctx(cost_cap_micros=SEARCH_COST_MICROS * 2),
        )
    finally:
        await connector._client.aclose()

    # This used to raise, and the raise escaped `search()` carrying off everything the earlier
    # calls had already been billed for. A live Delhi run spent $0.63 that way and stored
    # nothing: every task paid for its first tile, hit the cap on its second, and threw both away.
    assert found
    assert len(usage.calls) == 2


@respx.mock
async def test_a_budget_too_small_for_even_one_call_is_still_an_error() -> None:
    twenty = load_json("google_places", "search_success")["places"] * 7
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=_paged(twenty, "MORE")))
    connector, _ = make_connector()
    try:
        # Nothing was bought, so there is nothing to keep. Returning an empty list here would be
        # indistinguishable from "there are no cafes here", which is a different thing to say.
        with pytest.raises(BudgetExhaustedError):
            await connector.search(
                DiscoveryQuery(text="cafes", bbox=SMALL_BBOX, max_results=500),
                make_ctx(cost_cap_micros=SEARCH_COST_MICROS - 1),
            )
    finally:
        await connector._client.aclose()


# ---------------------------------------------------------------- place id cache


async def test_the_cache_keeps_whole_candidates_and_expires_within_the_terms() -> None:
    redis = fakeredis.FakeAsyncRedis()
    cache = PlaceSearchCache(redis)
    tile = Tile(*SMALL_BBOX)
    place = Place.model_validate(load_json("google_places", "search_success")["places"][0])
    candidate = to_candidate(place, datetime.now(UTC))
    assert candidate is not None

    assert await cache.get("google_places", "dentists", tile) is None
    await cache.put(
        "google_places", "dentists", tile, CachedSearch(candidates=[candidate], truncated=False)
    )

    hit = await cache.get("google_places", "dentists", tile)
    assert hit is not None
    # A place id alone is not a result: rehydrating one costs more than the search it saved.
    assert hit.candidates[0].name == candidate.name
    assert hit.candidates[0].observed_at == candidate.observed_at

    # And a candidate is not a result either, without the values behind it. Dropping these on
    # the way through the cache is what a live Delhi run exposed: 44 of 113 businesses came back
    # from cache with a name on the company row and no field values at all -- no provenance for
    # any of it -- and the job was charged for each one as a new lead.
    assert hit.candidates[0].values
    assert {v.field for v in hit.candidates[0].values} == {v.field for v in candidate.values}
    original = {v.field: v for v in candidate.values}
    for value in hit.candidates[0].values:
        was = original[value.field]
        assert (value.value, value.source_url, value.method, value.derivation) == (
            was.value,
            was.source_url,
            was.method,
            was.derivation,
        )
        assert value.observed_at == was.observed_at
        assert value.confidence == pytest.approx(was.confidence)

    ttl = await redis.ttl(cache_key("google_places", "dentists", tile))
    # Places content may be kept for 30 days, so the cache deletes itself on that clock.
    assert 0 < ttl <= PLACES_CONTENT_TTL_S
    await redis.aclose()


async def test_a_different_question_is_a_different_cache_entry() -> None:
    redis = fakeredis.FakeAsyncRedis()
    cache = PlaceSearchCache(redis)
    tile = Tile(*SMALL_BBOX)
    place = Place.model_validate(load_json("google_places", "search_success")["places"][0])
    candidate = to_candidate(place, datetime.now(UTC))
    assert candidate is not None
    await cache.put(
        "google_places", "dentists", tile, CachedSearch(candidates=[candidate], truncated=False)
    )

    assert await cache.get("google_places", "dentists\x1fdental_clinic", tile) is None
    assert await cache.get("google_places", "cafes", tile) is None
    assert await cache.get("google_places", "dentists", Tile(1.0, 1.0, 2.0, 2.0)) is None
    await redis.aclose()


@respx.mock
async def test_the_second_identical_search_is_free_and_returns_the_same_thing() -> None:
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_json("google_places", "search_success"))
    )
    redis = fakeredis.FakeAsyncRedis()
    connector, usage = make_connector(cache=PlaceSearchCache(redis))
    query = DiscoveryQuery(text="dental clinics", bbox=SMALL_BBOX, max_results=3)
    try:
        first = await connector.search(query, make_ctx())
        second = await connector.search(query, make_ctx())
    finally:
        await connector._client.aclose()
        await redis.aclose()

    assert route.call_count == 1
    assert len(usage.calls) == 1
    # The saving is worthless if the cached answer is not the answer.
    assert [c.name for c in second] == [c.name for c in first]
    assert [c.url for c in second] == [c.url for c in first]
    assert second[0].observed_at == first[0].observed_at


@respx.mock
async def test_a_search_with_a_different_filter_is_not_served_the_cached_one() -> None:
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_json("google_places", "search_success"))
    )
    redis = fakeredis.FakeAsyncRedis()
    connector, _ = make_connector(cache=PlaceSearchCache(redis))
    try:
        await connector.search(
            DiscoveryQuery(text="dentists", bbox=SMALL_BBOX, max_results=3), make_ctx()
        )
        await connector.search(
            DiscoveryQuery(
                text="dentists", bbox=SMALL_BBOX, category="dental_clinic", max_results=3
            ),
            make_ctx(),
        )
    finally:
        await connector._client.aclose()
        await redis.aclose()

    # The category changes the answer, so it has to change the key: otherwise one org receives
    # results computed under another org's filter.
    assert route.call_count == 2


# ---------------------------------------------------------------- registry wiring


def test_the_registry_offers_places_only_when_it_is_safe_to_run() -> None:
    redis = fakeredis.FakeAsyncRedis()
    ready, clients = build_connectors(
        settings=settings(GOOGLE_PLACES_API_KEY=API_KEY, GOOGLE_PLACES_ENABLED=True),
        redis=redis,
    )
    keyed_but_off, off_clients = build_connectors(
        settings=settings(GOOGLE_PLACES_API_KEY=API_KEY), redis=redis
    )
    without, no_clients = build_connectors(settings=settings(), redis=redis)

    assert ready.keys == ["google_places"]
    assert [c.key for c in ready.providing("phone")] == ["google_places"]
    assert len(clients) == 1

    # Places content has to be deleted on a 30-day clock (ADR-0011), so a key alone must not
    # start storing it: the switch stays off until the sweeper exists.
    assert keyed_but_off.keys == []
    assert off_clients == []

    # And a source that cannot run must never be offered to the planner as one that can.
    assert without.keys == []
    assert no_clients == []


def test_the_declared_source_policy_matches_the_seed_and_the_terms() -> None:
    connector = GooglePlacesConnector.__new__(GooglePlacesConnector)

    assert GooglePlacesConnector.tos_class == "green"
    assert GooglePlacesConnector.auth == "api_key"
    assert GooglePlacesConnector.meter == "api_google_places"
    # Places content may be kept for 30 days (ADR-0011); the seed row says the same.
    assert GooglePlacesConnector.default_ttl_days == 30
    assert connector.usable_in_production(legal_approved=False) is True


def test_the_field_masks_are_priced_where_the_cost_constants_say() -> None:
    search_enterprise = {f.removeprefix("places.") for f in SEARCH_FIELDS} & ENTERPRISE_FIELDS
    details_enterprise = set(DETAILS_FIELDS) & ENTERPRISE_FIELDS

    # A request is billed at the highest tier of any field it asks for, so a mask holding any
    # Enterprise field costs the Enterprise price. Pinned here because the mask is the price.
    assert search_enterprise, "SEARCH_FIELDS has no Enterprise field but is priced as Enterprise"
    assert details_enterprise
    assert SEARCH_COST_MICROS == 35_000
    assert DETAILS_COST_MICROS == 20_000


@respx.mock
async def test_a_place_that_no_longer_exists_is_its_own_kind_of_failure() -> None:
    respx.get(DETAILS_URL).mock(
        return_value=httpx.Response(
            404,
            json={"error": {"code": 404, "message": "Place not found.", "status": "NOT_FOUND"}},
        )
    )
    connector, _ = make_connector()
    try:
        # Cached ids go stale; Google's own advice is to refresh rather than treat it as a bug.
        with pytest.raises(PlaceNotFoundError):
            await connector.fetch(
                SourceRef(source_key="google_places", external_id="ChIJexampleAAAAAAAAAAAAAAAA1"),
                make_ctx(),
            )
    finally:
        await connector._client.aclose()


async def test_an_unusable_bounding_box_is_an_error_not_an_empty_answer() -> None:
    connector, _ = make_connector()
    try:
        # Returning [] here would read as "no businesses in this area".
        with pytest.raises(InvalidInputError, match="bounding box"):
            await connector.search(
                DiscoveryQuery(text="cafes", bbox=(19.0, 74.0, 18.0, 73.0)), make_ctx()
            )
    finally:
        await connector._client.aclose()


async def test_a_cache_entry_from_an_older_shape_is_never_read_back() -> None:
    redis = fakeredis.FakeAsyncRedis()
    cache = PlaceSearchCache(redis)
    tile = Tile(*SMALL_BBOX)

    # An entry that is merely out of shape is worse than no entry: it is served as a real answer
    # and silently lacks whatever the old shape did not carry. The key states which shape wrote
    # it, so a bump simply misses and the search is paid for again.
    assert f"v{SCHEMA_VERSION}:" in cache_key("google_places", "dentists", tile)
    stale = cache_key("google_places", "dentists", tile).replace(
        f"v{SCHEMA_VERSION}:", f"v{SCHEMA_VERSION - 1}:"
    )
    await redis.set(stale, '{"candidates": [], "truncated": false}')

    assert await cache.get("google_places", "dentists", tile) is None
    await redis.aclose()
