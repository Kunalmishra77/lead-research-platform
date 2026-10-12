"""Discovery from the place index.

Against a real index built in a tmp_path, because that is the whole connector: a SQLite read and
a mapping. Mocking the index would leave only the mapping tested, and the bugs this has already
produced -- an R*Tree query that dropped points on a box edge, a category match that found one
rung of a hierarchy -- all lived in the SQLite half.

No network, no key, no quota: there is nothing here to record a fixture of.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.connectors.places import OsmConnector, OvertureConnector
from app.connectors.types import DiscoveryQuery, SourceRef
from app.jobs.errors import InvalidInputError, ParseFailedError
from app.metering.context import CallContext
from app.places.index import IndexWriter, PlaceIndex
from app.places.model import OSM, OVERTURE, Place

from .connector_support import make_ctx

DELHI = (28.40, 76.83, 28.89, 77.35)
RELEASED = datetime(2026, 9, 23, tzinfo=UTC)


def _place(
    external_id: str,
    name: str,
    categories: tuple[str, ...],
    *,
    source: str = OVERTURE,
    lat: float = 28.45,
    lon: float = 77.10,
    **kw: object,
) -> Place:
    return Place(
        source=source,
        external_id=external_id,
        name=name,
        category=categories[-1],
        categories=categories,
        lat=lat,
        lon=lon,
        source_url=f"https://example.test/{external_id}",
        **kw,  # type: ignore[arg-type]
    )


@pytest.fixture
def index(tmp_path: Path) -> PlaceIndex:
    path = tmp_path / "places.sqlite"
    with IndexWriter(path) as writer:
        writer.add(
            [
                _place(
                    "o1",
                    "Anytime Fitness",
                    ("sports_and_recreation", "sport_or_fitness_facility", "gym"),
                    phone="097111 74449",
                    email="84.gurgaon@anytimefitness.in",
                    website="https://anytimefitness.in/gurugram",
                    socials=(
                        "https://www.facebook.com/212365016154267",
                        "https://www.instagram.com/anytimefitnessgurgaon",
                        "https://example.test/not-a-social",
                    ),
                    street="Elan Miracle",
                    housenumber="2nd floor",
                    city="Gurugram",
                    postcode="122004",
                    region="IN-HR",
                    country="IN",
                    confidence=0.8,
                    operating_status="OPEN",
                    updated_at=datetime(2026, 5, 4, tzinfo=UTC),
                ),
                _place(
                    "o2",
                    "Bikanervala",
                    ("food_and_drink", "restaurant", "indian_restaurant"),
                    phone="01141000000",
                ),
                _place("o3", "Unreachable Gym", ("sports_and_recreation", "gym")),
                # Mumbai: outside every Delhi box.
                _place("o4", "Marine Gym", ("gym",), lat=18.94, lon=72.82),
                # The other dataset, in the same file.
                _place("n99", "Iron Gym", ("leisure=fitness_centre",), source=OSM),
            ]
        )
        writer.finish(extract_date=RELEASED)
    return PlaceIndex(path)


@pytest.fixture
def ctx() -> CallContext:
    return make_ctx()


async def test_a_search_returns_the_businesses_in_the_box(
    index: PlaceIndex, ctx: CallContext
) -> None:
    found = await OvertureConnector(index).search(
        DiscoveryQuery(text="gyms", bbox=DELHI, options={"categories": ["gym"]}), ctx
    )
    assert {c.name for c in found} == {"Anytime Fitness", "Unreachable Gym"}
    assert all(c.source_key == OVERTURE for c in found)


async def test_a_category_matches_anywhere_in_the_hierarchy(
    index: PlaceIndex, ctx: CallContext
) -> None:
    """A request for restaurants has to reach the Indian one filed beneath it."""
    found = await OvertureConnector(index).search(
        DiscoveryQuery(text="restaurants", bbox=DELHI, options={"categories": ["restaurant"]}), ctx
    )
    assert [c.name for c in found] == ["Bikanervala"]


async def test_every_requested_category_is_used(index: PlaceIndex, ctx: CallContext) -> None:
    """The planner sends the whole list; reading only `category` would lose the rest.

    `category` carries the first for Places' single `includedType`, and a connector that read
    that field instead would quietly answer a two-category request with one category's results.
    """
    query = DiscoveryQuery(
        text="gyms and restaurants",
        bbox=DELHI,
        category="gym",
        options={"categories": ["gym", "restaurant"]},
    )
    found = await OvertureConnector(index).search(query, ctx)
    assert {c.name for c in found} == {"Anytime Fitness", "Unreachable Gym", "Bikanervala"}


async def test_a_request_with_no_category_falls_back_to_the_trade(
    index: PlaceIndex, ctx: CallContext
) -> None:
    """24 taxonomy entries have no Overture category, and this is what keeps them answerable."""
    found = await OvertureConnector(index).search(
        DiscoveryQuery(text="x", bbox=DELHI, options={"name_hint": "Bikanervala"}), ctx
    )
    assert [c.name for c in found] == ["Bikanervala"]


async def test_each_dataset_sees_only_its_own_rows(index: PlaceIndex, ctx: CallContext) -> None:
    """Both live in one index, and provenance has to name the one a value actually came from."""
    # Each is asked in its own dataset's vocabulary, which is the point: the taxonomy maps a
    # request to each source's category names (ADR-0017) and they do not share one.
    overture = await OvertureConnector(index).search(
        DiscoveryQuery(text="gyms", bbox=DELHI, options={"categories": ["gym"]}), ctx
    )
    osm = await OsmConnector(index).search(
        DiscoveryQuery(text="gyms", bbox=DELHI, options={"categories": ["leisure=fitness_centre"]}),
        ctx,
    )
    assert "Iron Gym" not in {c.name for c in overture}
    assert [c.name for c in osm] == ["Iron Gym"]
    assert osm[0].source_key == OSM


async def test_contactable_can_be_asked_for(index: PlaceIndex, ctx: CallContext) -> None:
    found = await OvertureConnector(index).search(
        DiscoveryQuery(
            text="gyms", bbox=DELHI, options={"categories": ["gym"], "contactable": True}
        ),
        ctx,
    )
    assert [c.name for c in found] == ["Anytime Fitness"]


async def test_a_website_can_be_required_of_the_source(
    index: PlaceIndex, ctx: CallContext
) -> None:
    """"Gyms in Delhi with a website" is a different market from "gyms in Delhi".

    Filtered in the index rather than after delivery, so the job's result cap is spent on rows
    the user asked for. The guarantee for sources that cannot filter lives in the handler.
    """
    found = await OvertureConnector(index).search(
        DiscoveryQuery(
            text="gyms", bbox=DELHI, options={"categories": ["gym"], "has_website": True}
        ),
        ctx,
    )
    assert [c.name for c in found] == ["Anytime Fitness"]


async def test_a_website_can_be_ruled_out_by_the_source(
    index: PlaceIndex, ctx: CallContext
) -> None:
    found = await OvertureConnector(index).search(
        DiscoveryQuery(
            text="gyms", bbox=DELHI, options={"categories": ["gym"], "has_website": False}
        ),
        ctx,
    )
    assert [c.name for c in found] == ["Unreachable Gym"]


async def test_not_asking_about_a_website_is_not_asking_for_none(
    index: PlaceIndex, ctx: CallContext
) -> None:
    """The option is three-state, and `bool()` on a missing one would mean `False`.

    That would silently turn every ordinary search into "and with no website" -- the opposite
    of what almost all of them want, and invisible in the result because the rows that came
    back are real businesses.
    """
    found = await OvertureConnector(index).search(
        DiscoveryQuery(text="gyms", bbox=DELHI, options={"categories": ["gym"]}), ctx
    )
    assert {c.name for c in found} == {"Anytime Fitness", "Unreachable Gym"}


async def test_a_search_without_a_box_is_refused(index: PlaceIndex, ctx: CallContext) -> None:
    """Every other source can fall back to the text. This one would mean "all of India"."""
    with pytest.raises(InvalidInputError, match="bounding box"):
        await OvertureConnector(index).search(DiscoveryQuery(text="gyms"), ctx)


async def test_a_candidate_carries_its_values_so_nothing_fetches_twice(
    index: PlaceIndex, ctx: CallContext
) -> None:
    [gym] = await OvertureConnector(index).search(
        DiscoveryQuery(text="x", bbox=DELHI, options={"categories": ["gym"], "contactable": True}),
        ctx,
    )
    values = {v.field: v.value for v in gym.values}
    assert values["name"] == "Anytime Fitness"
    assert values["phone"] == "097111 74449"
    assert values["email"] == "84.gurgaon@anytimefitness.in"
    assert values["address"] == "2nd floor Elan Miracle"
    assert values["city"] == "Gurugram"
    assert values["state"] == "IN-HR"
    assert values["country"] == "IN"
    assert values["postal_code"] == "122004"
    assert values["geo"] == {"lat": 28.45, "lng": 77.10}
    assert values["business_status"] == "open"
    # Socials are filed per platform, and the non-social URL is not a social.
    assert values["facebook"] == "https://www.facebook.com/212365016154267"
    assert values["instagram"] == "https://www.instagram.com/anytimefitnessgurgaon"
    assert "linkedin" not in values


async def test_every_value_carries_provenance(index: PlaceIndex, ctx: CallContext) -> None:
    """CLAUDE.md: no value without provenance, and a dataset is `provider`, not `api`."""
    [gym] = await OvertureConnector(index).search(
        DiscoveryQuery(text="x", bbox=DELHI, options={"categories": ["gym"], "contactable": True}),
        ctx,
    )
    for value in gym.values:
        assert value.source_key == OVERTURE
        assert value.source_url == "https://example.test/o1"
        assert value.method == "provider"
        assert value.derivation == "found"
        assert 0 < value.confidence <= 1
        # The record's own last-edited date, not the moment the index was read.
        assert value.observed_at == datetime(2026, 5, 4, tzinfo=UTC)


async def test_a_record_with_no_date_of_its_own_inherits_the_release(
    index: PlaceIndex, ctx: CallContext
) -> None:
    """Overture publishes no per-record timestamp, so the snapshot's date is the observation.

    Never `now()`: a September release read in December is still September's observation, and
    the freshness shown to a user has to say so.
    """
    [other] = await OvertureConnector(index).search(
        DiscoveryQuery(text="restaurants", bbox=DELHI, options={"categories": ["restaurant"]}), ctx
    )
    assert other.observed_at == RELEASED
    assert all(v.observed_at == RELEASED for v in other.values)


async def test_a_category_is_trusted_less_than_a_contact(
    index: PlaceIndex, ctx: CallContext
) -> None:
    """A dataset's category is its opinion; a phone number is a fact it recorded."""
    [gym] = await OvertureConnector(index).search(
        DiscoveryQuery(text="x", bbox=DELHI, options={"categories": ["gym"], "contactable": True}),
        ctx,
    )
    by_field = {v.field: v for v in gym.values}
    assert by_field["category"].confidence < by_field["phone"].confidence


async def test_a_record_can_be_read_back_by_its_id(index: PlaceIndex, ctx: CallContext) -> None:
    connector = OvertureConnector(index)
    raw = await connector.fetch(SourceRef(source_key=OVERTURE, external_id="o1"), ctx)
    assert raw.status == 200
    values = {v.field: v.value for v in connector.map(raw)}
    assert values["name"] == "Anytime Fitness"
    assert values["phone"] == "097111 74449"


async def test_a_record_that_is_not_there_says_so(index: PlaceIndex, ctx: CallContext) -> None:
    with pytest.raises(ParseFailedError, match="no record"):
        await OvertureConnector(index).fetch(
            SourceRef(source_key=OVERTURE, external_id="nope"), ctx
        )


async def test_health_reports_what_the_index_holds(index: PlaceIndex) -> None:
    health = await OvertureConnector(index).health()
    assert health.ok is True
    assert "2026-09-23" in (health.detail or "")


async def test_health_fails_loudly_when_the_index_is_missing(tmp_path: Path) -> None:
    """The realistic failure: a deployment without the volume mounted.

    Silent otherwise -- searches return nothing, which reads exactly like an area with no
    businesses in it.
    """
    health = await OvertureConnector(PlaceIndex(tmp_path / "absent.sqlite")).health()
    assert health.ok is False
    assert "no place index" in (health.detail or "")


async def test_health_fails_on_an_empty_index(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite"
    with IndexWriter(path) as writer:
        writer.finish()
    health = await OvertureConnector(PlaceIndex(path)).health()
    assert health.ok is False
    assert "empty" in (health.detail or "")


def test_the_datasets_declare_their_policy() -> None:
    for connector in (OvertureConnector, OsmConnector):
        assert connector.tos_class == "green"
        assert connector.auth == "none"
        assert connector.cost_per_call_micros == 0
    # Overture finds leads; OSM is the second opinion. Both are free, so a cost-ordered
    # `providing()` would otherwise break the tie alphabetically and lead with the thinner one.
    assert OvertureConnector.discovers is True
    assert OsmConnector.discovers is False


async def test_the_fallback_searches_the_trade_not_the_phrase(
    index: PlaceIndex, ctx: CallContext
) -> None:
    """The bug that made the first live run deliver nothing.

    With no category, the connector used to search names for `query.text` -- "gym in Gurgaon" --
    and no business is called that. The index held 553 gyms inside that very box and returned
    none of them. The planner now sends the trade on its own, because it is the only thing that
    can tell the trade from the place.
    """
    with_hint = DiscoveryQuery(text="gyms in Gurgaon", bbox=DELHI, options={"name_hint": "Gym"})
    assert {c.name for c in await OvertureConnector(index).search(with_hint, ctx)} == {
        "Unreachable Gym"
    }


async def test_naming_no_trade_at_all_returns_the_area(index: PlaceIndex, ctx: CallContext) -> None:
    """Deliberate, and the opposite of what the phrase-as-name version did.

    No category and no hint means the planner found no trade in the request, which happens when
    the user named none -- "businesses in Gurgaon". Returning the area answers that. The planner
    sends a hint whenever `filters.industry.include` holds anything, so this is not the path a
    request for dentists can fall down.
    """
    found = await OvertureConnector(index).search(
        DiscoveryQuery(text="businesses in Gurgaon", bbox=DELHI), ctx
    )
    assert len(found) == 3


async def test_a_category_beats_the_name_hint(index: PlaceIndex, ctx: CallContext) -> None:
    """A category is the precise answer; the hint only covers the 24 entries that have none.

    Worth pinning: matching both would quietly narrow every search to businesses with the trade
    in their name, and most do not -- "Vanarshala" is a gym.
    """
    query = DiscoveryQuery(
        text="gyms", bbox=DELHI, options={"categories": ["gym"], "name_hint": "Gym"}
    )
    found = await OvertureConnector(index).search(query, ctx)
    assert {c.name for c in found} == {"Anytime Fitness", "Unreachable Gym"}


async def test_every_value_carries_a_url_the_database_will_accept(
    index: PlaceIndex, ctx: CallContext
) -> None:
    """`field_values.source_url` is checked against `^https?://` by Postgres (migration 0013).

    Nothing here caught that the Overture values carried an `s3://` URI, because the worker
    tests store through a fake graph and the live-database suite skips without a DATABASE_URL.
    The first real run found its leads and then failed every insert, so the job reported zero
    and looked exactly like a search that had found nothing.

    The rule is the database's, so this mirrors it rather than restating it loosely: a URL a
    person cannot open is not provenance.
    """
    found = await OvertureConnector(index).search(
        DiscoveryQuery(text="x", bbox=DELHI, options={"categories": ["gym", "restaurant"]}), ctx
    )
    assert found
    for candidate in found:
        assert candidate.source_url.startswith(("http://", "https://")), candidate.source_url
        assert candidate.values
        for value in candidate.values:
            assert value.source_url.startswith(("http://", "https://")), value.source_url
            assert len(value.source_url) <= 2048


def test_the_overture_release_url_is_openable() -> None:
    """Points at the release, because Overture publishes no page per record.

    Its own documentation says to look an id up by querying the release's Parquet, and
    `external_id` carries that id. Over https rather than the `s3://` form DuckDB reads.
    """
    from app.places.overture import DEFAULT_RELEASE, places_url, release_url  # noqa: PLC0415

    assert release_url().startswith("https://")
    assert DEFAULT_RELEASE in release_url()
    # The reader still gets the s3 form; the two must not be confused for each other.
    assert places_url().startswith("s3://")
