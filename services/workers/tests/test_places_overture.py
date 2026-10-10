"""Mapping Overture Places rows into the place index.

Against real DuckDB holding a real table of Overture's own column types -- nested structs, lists
of strings, a list of address structs. Not a stub: the mapping's whole job is pulling values out
of that shape, and a dict standing in for it would agree with whatever the code already does.

No test reaches the network (docs/08). `build_index` takes a connection, and these pass one
holding a local `places_src` table.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from app.places.index import PlaceIndex
from app.places.overture import DEFAULT_RELEASE, build_index

#: Overture's places columns, as `describe` reports them on the real dataset.
_DDL = """
create table places_src (
  id               varchar,
  names            struct("primary" varchar),
  basic_category   varchar,
  taxonomy         struct("primary" varchar),
  bbox             struct(xmin double, xmax double, ymin double, ymax double),
  phones           varchar[],
  emails           varchar[],
  websites         varchar[],
  socials          varchar[],
  addresses        struct(freeform varchar, locality varchar, postcode varchar,
                          region varchar, country varchar)[],
  confidence       double,
  operating_status varchar
)
"""


def _row(
    place_id: str,
    name: str,
    category: str,
    *,
    lat: float = 28.45,
    lon: float = 77.10,
    phones: list[str] | None = None,
    emails: list[str] | None = None,
    websites: list[str] | None = None,
    socials: list[str] | None = None,
    confidence: float = 0.9,
    operating_status: str | None = "open",
    address: dict[str, str] | None = None,
) -> tuple[Any, ...]:
    addr = address or {
        "freeform": "2nd floor, Elan Miracle",
        "locality": "Gurugram",
        "postcode": "122004",
        "region": "IN-HR",
        "country": "IN",
    }
    return (
        place_id,
        {"primary": name},
        category,
        {"primary": category},
        # A point's bbox has equal corners; the reader takes the centre either way.
        {"xmin": lon, "xmax": lon, "ymin": lat, "ymax": lat},
        phones,
        emails,
        websites,
        socials,
        [addr],
        confidence,
        operating_status,
    )


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute(_DDL)
    return connection


def _insert(con: duckdb.DuckDBPyConnection, rows: list[tuple[Any, ...]]) -> None:
    placeholders = ", ".join("?" * 12)
    con.executemany(f"insert into places_src values ({placeholders})", rows)  # noqa: S608


def test_a_business_maps_with_everything_it_carries(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    _insert(
        con,
        [
            _row(
                "08f2ab",
                "Anytime Fitness",
                "gym",
                phones=["097111 74449"],
                emails=["84.gurgaon@anytimefitness.in"],
                websites=["https://www.anytimefitness.com/en-in/locations/gurugram"],
                socials=["https://www.facebook.com/anytimefitnessgurgaon"],
            )
        ],
    )
    out = tmp_path / "overture.sqlite"
    report = build_index(out, bbox=(28.0, 77.0, 29.0, 78.0), connection=con)

    assert report.places == 1
    [place] = PlaceIndex(out).search(bbox=(28.0, 77.0, 29.0, 78.0))
    assert place.source == "overture"
    assert place.external_id == "08f2ab"
    assert place.name == "Anytime Fitness"
    assert place.category == "gym"
    assert place.phone == "097111 74449"
    assert place.email == "84.gurgaon@anytimefitness.in"
    assert place.website == "https://www.anytimefitness.com/en-in/locations/gurugram"
    assert place.socials == ("https://www.facebook.com/anytimefitnessgurgaon",)
    assert place.street == "2nd floor, Elan Miracle"
    assert place.city == "Gurugram"
    assert place.postcode == "122004"
    assert place.region == "IN-HR"
    assert place.country == "IN"
    assert place.confidence == pytest.approx(0.9)
    assert place.operating_status == "open"
    assert DEFAULT_RELEASE in place.source_url


def test_machines_are_not_leads(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """An ATM carries its bank's national helpline, so admitting it pads two numbers at once.

    1.0% of Delhi's rows, and every one of them would have arrived with a phone number that
    nobody wants to be called on.
    """
    _insert(
        con,
        [
            _row("a1", "Axis Bank ATM", "atm", phones=["+9118605005555"]),
            _row("a2", "Axis Bank Branch", "bank_or_credit_union", phones=["+9118605005555"]),
        ],
    )
    out = tmp_path / "overture.sqlite"
    report = build_index(out, bbox=(28.0, 77.0, 29.0, 78.0), connection=con)

    assert report.places == 1
    assert report.not_a_business == 1
    assert [p.name for p in PlaceIndex(out).search(bbox=(28.0, 77.0, 29.0, 78.0))] == [
        "Axis Bank Branch"
    ]


def test_a_closed_business_is_not_a_lead(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """A closed shop with a perfect phone number is worse than no lead at all."""
    _insert(
        con,
        [
            _row("c1", "Gone Cafe", "cafe", operating_status="closed", phones=["01141000000"]),
            _row("c2", "Open Cafe", "cafe", operating_status="open", phones=["01141000001"]),
        ],
    )
    out = tmp_path / "overture.sqlite"
    report = build_index(out, bbox=(28.0, 77.0, 29.0, 78.0), connection=con)
    assert report.places == 1
    assert [p.name for p in PlaceIndex(out).search(bbox=(28.0, 77.0, 29.0, 78.0))] == ["Open Cafe"]


def test_a_column_shifted_row_loses_its_contacts_but_keeps_its_name(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """The real corruption found in Delhi: every contact field shifted one place left.

    `IN-HR` is the region, `IN` the country, `122004` the postcode. Each landed in the field to
    its left. Validating only the phone would have stored a postcode as this company's website,
    which is worse than storing nothing: a missing value is honestly incomplete, a wrong one
    sends someone to a dead end and looks like our mistake.
    """
    _insert(
        con,
        [
            _row(
                "k1",
                "Kotak Mahindra",
                "bank_or_credit_union",
                phones=["IN-HR"],
                emails=["IN"],
                websites=["122004"],
            )
        ],
    )
    out = tmp_path / "overture.sqlite"
    report = build_index(out, bbox=(28.0, 77.0, 29.0, 78.0), connection=con)

    assert report.bad_phone == 1
    assert report.bad_email == 1
    assert report.bad_website == 1
    [place] = PlaceIndex(out).search(bbox=(28.0, 77.0, 29.0, 78.0))
    assert place.name == "Kotak Mahindra"
    assert place.phone is None
    assert place.email is None
    assert place.website is None


def test_a_place_is_pinned_at_the_centre_of_its_box(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Taking a corner instead would put every non-point place off by half its own extent."""
    rows = [_row("p1", "Big Mall", "shopping_center")]
    shifted = list(rows[0])
    shifted[4] = {"xmin": 77.20, "xmax": 77.21, "ymin": 28.50, "ymax": 28.51}
    _insert(con, [tuple(shifted)])

    out = tmp_path / "overture.sqlite"
    build_index(out, bbox=(28.0, 77.0, 29.0, 78.0), connection=con)
    [place] = PlaceIndex(out).search(bbox=(28.0, 77.0, 29.0, 78.0))
    assert place.lat == pytest.approx(28.505, abs=1e-6)
    assert place.lon == pytest.approx(77.205, abs=1e-6)


def test_the_bbox_bounds_what_is_extracted(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    _insert(
        con,
        [
            _row("d1", "Delhi Gym", "gym", lat=28.45, lon=77.10),
            _row("m1", "Mumbai Gym", "gym", lat=18.94, lon=72.82),
        ],
    )
    out = tmp_path / "overture.sqlite"
    report = build_index(out, bbox=(28.0, 77.0, 29.0, 78.0), connection=con)
    assert report.places == 1
    assert [p.name for p in PlaceIndex(out).search(bbox=(0.0, 0.0, 90.0, 180.0))] == ["Delhi Gym"]


def test_low_confidence_rows_can_be_excluded(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    _insert(
        con,
        [
            _row("s1", "Sure Thing", "gym", confidence=0.95),
            _row("s2", "Maybe", "gym", confidence=0.10),
        ],
    )
    out = tmp_path / "overture.sqlite"
    report = build_index(out, bbox=(28.0, 77.0, 29.0, 78.0), min_confidence=0.5, connection=con)
    assert report.places == 1
    assert [p.name for p in PlaceIndex(out).search(bbox=(28.0, 77.0, 29.0, 78.0))] == ["Sure Thing"]


def test_the_report_counts_reachability(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """These counts are the product's headline claim, so they come from the build, not a guess."""
    _insert(
        con,
        [
            _row("r1", "Phone Only", "gym", phones=["01141000000"]),
            _row("r2", "Email Only", "gym", emails=["hi@example.in"]),
            _row(
                "r3",
                "Both",
                "gym",
                phones=["01141000001"],
                emails=["hello@example.in"],
                websites=["https://example.in"],
            ),
            _row("r4", "Neither", "gym"),
        ],
    )
    report = build_index(tmp_path / "o.sqlite", bbox=(28.0, 77.0, 29.0, 78.0), connection=con)
    assert report.places == 4
    assert report.with_phone == 2
    assert report.with_email == 2
    assert report.with_website == 1


def test_contactable_is_a_filter_the_index_answers(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    _insert(
        con,
        [
            _row("f1", "Reachable", "gym", phones=["01141000000"]),
            _row("f2", "Unreachable", "gym"),
        ],
    )
    out = tmp_path / "overture.sqlite"
    build_index(out, bbox=(28.0, 77.0, 29.0, 78.0), connection=con)
    index = PlaceIndex(out)
    box = (28.0, 77.0, 29.0, 78.0)
    assert [p.name for p in index.search(bbox=box, contactable=True)] == ["Reachable"]
    assert index.count(bbox=box, contactable=True) == 1
    assert index.count(bbox=box) == 2


def test_a_rebuild_replaces_rather_than_accumulates(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """A monthly refresh runs this repeatedly; the count must not climb each time."""
    _insert(con, [_row("x1", "Only One", "gym")])
    out = tmp_path / "overture.sqlite"
    build_index(out, bbox=(28.0, 77.0, 29.0, 78.0), connection=con)
    build_index(out, bbox=(28.0, 77.0, 29.0, 78.0), connection=con)
    assert PlaceIndex(out).stats().places == 1
    assert not out.with_suffix(out.suffix + ".building").exists()


def test_the_index_records_the_release_its_data_is_from(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Without this a six-month-old snapshot looks exactly like today's data."""
    _insert(con, [_row("x1", "Somewhere", "gym")])
    out = tmp_path / "overture.sqlite"
    build_index(out, bbox=(28.0, 77.0, 29.0, 78.0), release="2026-09-23.1", connection=con)
    assert PlaceIndex(out).stats().extract_date == datetime(2026, 9, 23, tzinfo=UTC)
