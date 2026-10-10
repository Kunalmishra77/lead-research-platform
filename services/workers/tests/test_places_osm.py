"""Reading an extract into an index, against a real `.osm.pbf`.

The fixture writes an actual PBF with osmium's own writer rather than feeding the parser a stand-in.
That matters here more than usual: two of the things most likely to be wrong -- whether a tag
filter starves the node-location cache that way centroids depend on, and whether a closed way's
repeated final node skews its centre -- are invisible to anything that does not go through the
real decoder. Both were wrong at first and both were found this way.
"""

from pathlib import Path

import osmium
import pytest
from osmium.osm import mutable

from app.places.index import PlaceIndex
from app.places.osm import build_index

#: A square building outline. Its centre is lat 28.505, lon 77.205.
_SQUARE = [(77.20, 28.50), (77.21, 28.50), (77.21, 28.51), (77.20, 28.51)]


@pytest.fixture
def extract(tmp_path: Path) -> Path:
    """A small extract holding one of each case the ingest has to get right."""
    path = tmp_path / "test.osm.pbf"
    writer = osmium.SimpleWriter(str(path))

    # A point business, fully tagged.
    writer.add_node(
        mutable.Node(
            id=1,
            location=(77.10, 28.45),
            version=1,
            tags={
                "name": "Iron Gym",
                "leisure": "fitness_centre",
                "phone": "+91 98100 00000",
                "website": "https://irongym.in",
                "addr:city": "Gurgaon",
            },
        )
    )
    # Named infrastructure: carries a primary tag and a name, and is still not a lead.
    writer.add_node(
        mutable.Node(
            id=2,
            location=(77.11, 28.46),
            version=1,
            tags={"name": "Gandhi Chowk Parking", "amenity": "parking"},
        )
    )
    # A business with no name.
    writer.add_node(mutable.Node(id=3, location=(77.12, 28.47), version=1, tags={"shop": "bakery"}))
    # Not a business at all; filtered out before the tag rules even see it.
    writer.add_node(
        mutable.Node(id=4, location=(77.13, 28.48), version=1, tags={"highway": "street_lamp"})
    )

    # The corners of a building, then the building itself as a closed way.
    for i, (lon, lat) in enumerate(_SQUARE, start=10):
        writer.add_node(mutable.Node(id=i, location=(lon, lat), version=1))
    writer.add_way(
        mutable.Way(
            id=100,
            nodes=[10, 11, 12, 13, 10],
            version=1,
            tags={"name": "Fit Factory", "leisure": "fitness_centre"},
        )
    )
    # A way whose corners are not in this extract, as happens at an extract's edge.
    writer.add_way(
        mutable.Way(
            id=101,
            nodes=[9001, 9002, 9003],
            version=1,
            tags={"name": "Cut Off Cafe", "amenity": "cafe"},
        )
    )
    writer.close()
    return path


def test_ingest_finds_the_businesses_and_leaves_the_rest(extract: Path, tmp_path: Path) -> None:
    out = tmp_path / "index.sqlite"
    report = build_index(extract, out)

    assert report.places == 2
    assert {p.name for p in PlaceIndex(out).search(bbox=(28.0, 77.0, 29.0, 78.0))} == {
        "Iron Gym",
        "Fit Factory",
    }
    # The named car park and the unnamed bakery both reached the tag rules and were refused.
    assert report.not_a_business == 2
    # The way at the edge could not be placed, so it is counted rather than silently dropped.
    assert report.ways_without_location == 1


def test_a_closed_ways_centre_is_not_pulled_by_its_repeated_node(
    extract: Path, tmp_path: Path
) -> None:
    """The square's centre is 28.505, 77.205.

    Averaging the node list as given double-weights the first corner, because a closed way
    repeats it as its last. That put an earlier version at 28.504, 77.204 -- wrong by about
    110 m, and wrong in the same direction for every building in India.
    """
    out = tmp_path / "index.sqlite"
    build_index(extract, out)
    [factory] = PlaceIndex(out).search(bbox=(28.0, 77.0, 29.0, 78.0), name="Fit Factory")
    assert factory.lat == pytest.approx(28.505, abs=1e-6)
    assert factory.lon == pytest.approx(77.205, abs=1e-6)


def test_tag_values_survive_the_whole_pass(extract: Path, tmp_path: Path) -> None:
    out = tmp_path / "index.sqlite"
    build_index(extract, out)
    [gym] = PlaceIndex(out).search(bbox=(28.0, 77.0, 29.0, 78.0), name="Iron Gym")
    assert gym.source == "osm"
    assert gym.external_id == "n1"
    assert gym.category == "leisure=fitness_centre"
    assert gym.phone == "+91 98100 00000"
    assert gym.website == "https://irongym.in"
    assert gym.city == "Gurgaon"
    assert gym.source_url == "https://www.openstreetmap.org/node/1"


def test_the_report_counts_what_it_read(extract: Path, tmp_path: Path) -> None:
    """The counts are how an operator tells a bad extract from bad tag rules."""
    report = build_index(extract, tmp_path / "index.sqlite")
    # Four tagged nodes reach the filter; the street lamp does not carry a primary key.
    assert report.nodes == 3
    assert report.ways == 2
    assert report.relations_skipped == 0
    assert report.by_category == {"leisure=fitness_centre": 2}


def test_a_rebuild_replaces_the_index_rather_than_adding_to_it(
    extract: Path, tmp_path: Path
) -> None:
    """A monthly refresh runs this over and over; the count must not climb each time."""
    out = tmp_path / "index.sqlite"
    build_index(extract, out)
    build_index(extract, out)
    assert PlaceIndex(out).stats().places == 2


def test_a_build_leaves_no_staging_file_behind(extract: Path, tmp_path: Path) -> None:
    out = tmp_path / "index.sqlite"
    build_index(extract, out)
    assert out.is_file()
    assert not out.with_suffix(out.suffix + ".building").exists()


def test_a_missing_extract_says_so(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_index(tmp_path / "absent.osm.pbf", tmp_path / "index.sqlite")


def test_the_index_records_where_its_data_is_from(extract: Path, tmp_path: Path) -> None:
    """Without this a stale snapshot looks exactly like today's data."""
    out = tmp_path / "index.sqlite"
    build_index(extract, out)
    assert PlaceIndex(out).stats().extract_date is not None
