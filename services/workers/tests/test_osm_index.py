"""The place index: bounding-box and category queries against a real SQLite file.

Built in a tmp_path rather than mocked, because the thing most likely to be wrong is the R*Tree
join and the bbox comparison operators, and a mock of SQLite would agree with whatever the code
does. These are the queries discovery runs, run for real.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.osm.index import SCHEMA_VERSION, IndexWriter, OsmIndex
from app.osm.places import Place

#: Roughly Delhi and its neighbours, so the fixtures read like the real thing.
DELHI = (28.40, 76.83, 28.89, 77.35)


def _place(osm_id: int, name: str, category: str, lat: float, lon: float, **kw: object) -> Place:
    return Place(osm_type="n", osm_id=osm_id, name=name, category=category, lat=lat, lon=lon, **kw)


@pytest.fixture
def index(tmp_path: Path) -> OsmIndex:
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
    return OsmIndex(path)


def test_a_bbox_returns_what_is_inside_it_and_nothing_else(index: OsmIndex) -> None:
    names = {p.name for p in index.search(bbox=DELHI)}
    assert names == {"Iron Gym", "Fit Factory", "Sharma Bakery", "100% Pure Juice"}
    assert "Marine Gym" not in names


def test_a_bbox_excludes_a_place_just_outside_its_edge(index: OsmIndex) -> None:
    """The boundary is where an off-by-one in the R*Tree comparison would show up."""
    # Iron Gym is at 28.45, 77.10. A box ending just short of it must not return it.
    assert index.search(bbox=(28.40, 76.83, 28.44, 77.35)) == []
    # A box ending exactly on it must, since the bounds are inclusive.
    assert [p.name for p in index.search(bbox=(28.40, 76.83, 28.45, 77.35))] == ["Iron Gym"]


def test_categories_narrow_the_search(index: OsmIndex) -> None:
    gyms = index.search(bbox=DELHI, categories=["leisure=fitness_centre"])
    assert {p.name for p in gyms} == {"Iron Gym", "Fit Factory"}

    several = index.search(bbox=DELHI, categories=["shop=bakery", "shop=beverages"])
    assert {p.name for p in several} == {"Sharma Bakery", "100% Pure Juice"}

    assert index.search(bbox=DELHI, categories=["shop=nothing_like_this"]) == []


def test_a_name_containing_a_wildcard_matches_literally(index: OsmIndex) -> None:
    """`100% Pure Juice` has a LIKE wildcard in it. Unescaped, `%` matches anything."""
    assert [p.name for p in index.search(bbox=DELHI, name="100%")] == ["100% Pure Juice"]
    # The proof it is escaped: a name that would match only if `%` were a wildcard finds nothing.
    assert index.search(bbox=DELHI, name="100%Juice") == []


def test_name_search_is_a_substring_match(index: OsmIndex) -> None:
    assert [p.name for p in index.search(bbox=DELHI, name="Gym")] == ["Iron Gym"]
    assert [p.name for p in index.search(bbox=DELHI, name="Bakery")] == ["Sharma Bakery"]


def test_count_agrees_with_search(index: OsmIndex) -> None:
    """Counting is what lets a sweep be planned before it is run, so it must not drift."""
    assert index.count(bbox=DELHI) == 4
    assert index.count(bbox=DELHI, categories=["leisure=fitness_centre"]) == 2
    assert index.count(bbox=(18.0, 72.0, 19.5, 73.5)) == 1


def test_paging_is_stable_and_covers_everything_once(index: OsmIndex) -> None:
    """Ordered by id, so page two never repeats or skips a business from page one."""
    first = index.search(bbox=DELHI, limit=2, offset=0)
    second = index.search(bbox=DELHI, limit=2, offset=2)
    assert len(first) == 2
    assert len(second) == 2
    ids = [p.osm_id for p in (*first, *second)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 4


def test_a_place_survives_the_round_trip(index: OsmIndex) -> None:
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
    [back] = OsmIndex(path.with_name("one.sqlite")).search(bbox=DELHI)
    assert back.phone == "+911141000000"
    assert back.website == "https://example.in"
    assert back.email == "hi@example.in"
    assert back.street == "MG Road"
    assert back.housenumber == "12A"
    assert back.city == "Gurgaon"
    assert back.postcode == "122002"
    assert back.updated_at == datetime(2026, 3, 4, tzinfo=UTC)


def test_stats_report_what_the_index_holds_and_when_the_data_is_from(index: OsmIndex) -> None:
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
    assert OsmIndex(path).stats().places == 1


def test_a_missing_index_reports_itself_missing(tmp_path: Path) -> None:
    assert OsmIndex(tmp_path / "nope.sqlite").exists() is False
