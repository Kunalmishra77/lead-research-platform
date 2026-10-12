"""The place index: bounding-box and category queries against a real SQLite file.

Built in a tmp_path rather than mocked, because the thing most likely to be wrong is the R*Tree
join and the bbox comparison operators, and a mock of SQLite would agree with whatever the code
does. These are the queries discovery runs, run for real.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.places.index import SCHEMA_VERSION, IndexWriter, PlaceIndex
from app.places.model import OSM, Place

#: Roughly Delhi and its neighbours, so the fixtures read like the real thing.
DELHI = (28.40, 76.83, 28.89, 77.35)


def _place(osm_id: int, name: str, category: str, lat: float, lon: float, **kw: object) -> Place:
    return Place(
        source=OSM,
        external_id=f"n{osm_id}",
        name=name,
        category=category,
        categories=(category,),
        lat=lat,
        lon=lon,
        **kw,
    )


@pytest.fixture
def index(tmp_path: Path) -> PlaceIndex:
    """An index holding five businesses: four inside Delhi's box, one well outside it."""
    path = tmp_path / "test.sqlite"
    with IndexWriter(path) as writer:
        writer.add(
            [
                _place(1, "Iron Gym", "leisure=fitness_centre", 28.45, 77.10),
                _place(2, "Fit Factory", "leisure=fitness_centre", 28.60, 77.20),
                _place(3, "Sharma Bakery", "shop=bakery", 28.55, 77.15),
                _place(4, "100% Pure Juice", "shop=beverages", 28.50, 77.05),
                # Mumbai: inside no Delhi box.
                _place(5, "Marine Gym", "leisure=fitness_centre", 18.94, 72.82),
            ]
        )
        writer.finish(extract_date=datetime(2026, 10, 9, tzinfo=UTC))
    return PlaceIndex(path)


def test_a_bbox_returns_what_is_inside_it_and_nothing_else(index: PlaceIndex) -> None:
    names = {p.name for p in index.search(bbox=DELHI)}
    assert names == {"Iron Gym", "Fit Factory", "Sharma Bakery", "100% Pure Juice"}
    assert "Marine Gym" not in names


def test_a_bbox_excludes_a_place_just_outside_its_edge(index: PlaceIndex) -> None:
    """The boundary is where an off-by-one in the R*Tree comparison would show up."""
    # Iron Gym is at 28.45, 77.10. A box ending just short of it must not return it.
    assert index.search(bbox=(28.40, 76.83, 28.44, 77.35)) == []
    # A box ending exactly on it must, since the bounds are inclusive.
    assert [p.name for p in index.search(bbox=(28.40, 76.83, 28.45, 77.35))] == ["Iron Gym"]


def test_categories_narrow_the_search(index: PlaceIndex) -> None:
    gyms = index.search(bbox=DELHI, categories=["leisure=fitness_centre"])
    assert {p.name for p in gyms} == {"Iron Gym", "Fit Factory"}

    several = index.search(bbox=DELHI, categories=["shop=bakery", "shop=beverages"])
    assert {p.name for p in several} == {"Sharma Bakery", "100% Pure Juice"}

    assert index.search(bbox=DELHI, categories=["shop=nothing_like_this"]) == []


def test_a_name_containing_a_wildcard_matches_literally(index: PlaceIndex) -> None:
    """`100% Pure Juice` has a LIKE wildcard in it. Unescaped, `%` matches anything."""
    assert [p.name for p in index.search(bbox=DELHI, name="100%")] == ["100% Pure Juice"]
    # The proof it is escaped: a name that would match only if `%` were a wildcard finds nothing.
    assert index.search(bbox=DELHI, name="100%Juice") == []


def test_name_search_is_a_substring_match(index: PlaceIndex) -> None:
    assert [p.name for p in index.search(bbox=DELHI, name="Gym")] == ["Iron Gym"]
    assert [p.name for p in index.search(bbox=DELHI, name="Bakery")] == ["Sharma Bakery"]


def test_count_agrees_with_search(index: PlaceIndex) -> None:
    """Counting is what lets a sweep be planned before it is run, so it must not drift."""
    assert index.count(bbox=DELHI) == 4
    assert index.count(bbox=DELHI, categories=["leisure=fitness_centre"]) == 2
    assert index.count(bbox=(18.0, 72.0, 19.5, 73.5)) == 1


def test_paging_is_stable_and_covers_everything_once(index: PlaceIndex) -> None:
    """Ordered by id, so page two never repeats or skips a business from page one."""
    first = index.search(bbox=DELHI, limit=2, offset=0)
    second = index.search(bbox=DELHI, limit=2, offset=2)
    assert len(first) == 2
    assert len(second) == 2
    ids = [p.external_id for p in (*first, *second)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 4


def test_a_place_survives_the_round_trip(index: PlaceIndex) -> None:
    path = index.path
    with IndexWriter(path.with_name("one.sqlite")) as writer:
        writer.add(
            [
                _place(
                    9,
                    "Full Record",
                    "healthcare=dentist",
                    28.5,
                    77.1,
                    phone="+911141000000",
                    website="https://example.in",
                    email="hi@example.in",
                    street="MG Road",
                    housenumber="12A",
                    city="Gurgaon",
                    postcode="122002",
                    updated_at=datetime(2026, 3, 4, tzinfo=UTC),
                )
            ]
        )
        writer.finish()
    [back] = PlaceIndex(path.with_name("one.sqlite")).search(bbox=DELHI)
    assert back.phone == "+911141000000"
    assert back.website == "https://example.in"
    assert back.email == "hi@example.in"
    assert back.street == "MG Road"
    assert back.housenumber == "12A"
    assert back.city == "Gurgaon"
    assert back.postcode == "122002"
    assert back.updated_at == datetime(2026, 3, 4, tzinfo=UTC)


def test_stats_report_what_the_index_holds_and_when_the_data_is_from(index: PlaceIndex) -> None:
    stats = index.stats()
    assert stats.places == 5
    assert stats.schema_version == SCHEMA_VERSION
    assert stats.extract_date == datetime(2026, 10, 9, tzinfo=UTC)
    assert stats.built_at is not None


def test_the_same_element_is_not_stored_twice(tmp_path: Path) -> None:
    """Re-adding an element must not duplicate it: a build that is retried stays correct."""
    path = tmp_path / "dupes.sqlite"
    with IndexWriter(path) as writer:
        writer.add([_place(1, "Iron Gym", "leisure=fitness_centre", 28.45, 77.10)])
        writer.add([_place(1, "Iron Gym", "leisure=fitness_centre", 28.45, 77.10)])
        writer.finish()
    assert PlaceIndex(path).stats().places == 1


def test_a_missing_index_reports_itself_missing(tmp_path: Path) -> None:
    assert PlaceIndex(tmp_path / "nope.sqlite").exists() is False


@pytest.fixture
def mixed(tmp_path: Path) -> PlaceIndex:
    """Four restaurants in Delhi, two of which record a website."""
    path = tmp_path / "mixed.sqlite"
    with IndexWriter(path) as writer:
        writer.add(
            [
                _place(11, "Online Diner", "amenity=restaurant", 28.50, 77.10,
                       website="https://diner.example.in"),
                _place(12, "Wired Dhaba", "amenity=restaurant", 28.55, 77.12,
                       website="https://dhaba.example.in"),
                _place(13, "Offline Dhaba", "amenity=restaurant", 28.60, 77.14),
                _place(14, "No Site Cafe", "amenity=restaurant", 28.65, 77.16),
            ]
        )
        writer.finish(extract_date=datetime(2026, 10, 9, tzinfo=UTC))
    return PlaceIndex(path)


def test_a_website_can_be_required(mixed: PlaceIndex) -> None:
    found = mixed.search(bbox=DELHI, has_website=True)
    assert {p.name for p in found} == {"Online Diner", "Wired Dhaba"}


def test_a_website_can_be_ruled_out(mixed: PlaceIndex) -> None:
    """The other half of the filter, and a market of its own: who to sell a website to."""
    found = mixed.search(bbox=DELHI, has_website=False)
    assert {p.name for p in found} == {"Offline Dhaba", "No Site Cafe"}


def test_not_asking_about_websites_returns_both_kinds(mixed: PlaceIndex) -> None:
    """`None` is not `False`. Collapsing the two would make every unfiltered search mean
    "without a website", which is the opposite of what most of them want."""
    assert len(mixed.search(bbox=DELHI)) == 4
    assert len(mixed.search(bbox=DELHI, has_website=None)) == 4


def test_counting_applies_the_same_website_filter_as_searching(mixed: PlaceIndex) -> None:
    """An estimate has to count the market a run would deliver, not a larger one.

    `count` feeds the feasibility badge and the planner's sweep size. Counting every restaurant
    and then delivering only those with a website is how a badge comes to promise double.
    """
    assert mixed.count(bbox=DELHI) == 4
    assert mixed.count(bbox=DELHI, has_website=True) == 2
    assert mixed.count(bbox=DELHI, has_website=False) == 2
    assert mixed.count(bbox=DELHI, has_website=True) == len(
        mixed.search(bbox=DELHI, has_website=True)
    )
