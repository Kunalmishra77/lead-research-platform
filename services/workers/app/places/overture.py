"""Reading Overture Maps Places into the place index (ADR-0017).

Overture publishes GeoParquet to public object storage, monthly, with no account and no quota.
DuckDB reads it in place and pushes the bounding box down into the Parquet row-group statistics,
so extracting one country never downloads the rest of the world.

Why this and not OpenStreetMap, measured on the same Delhi box:

    OSM        21,879 businesses    9.7% phone    2.7% email
    Overture  283,302 businesses   67.1% own phone   38.0% own email

"Own" is the number that matters. 80.1% of Overture rows have a phone field filled, but 200
numbers cover 16,099 places -- chains repeating one switchboard -- so counting filled fields
overstates reachable businesses by about a fifth. This module counts, and reports, both.

Two kinds of row are refused here rather than stored and explained later:

  * machines and infrastructure (`atm`, `bus_stop`, ...), 1.0% of Delhi, whose phone number is
    their owner's national helpline;
  * column-shifted rows, 0.6%, where a region code sits in the phone field. A junk contact is
    worse than a missing one: missing is honestly incomplete, junk makes someone dial `IN-HR`
    and conclude the product is broken.

Licence: CDLA-Permissive-2.0 for most sources, Apache 2.0 for Foursquare-derived rows, CC0 for
AllThePlaces. Commercial use is permitted; sharing the data obliges us to ship the agreement text
(`docs/licenses/CDLA-Permissive-2.0.md`) with any export built from it.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import structlog

from app.places.index import IndexWriter
from app.places.model import (
    OVERTURE,
    Place,
    is_business,
    looks_like_email,
    looks_like_phone,
    looks_like_url,
)

log = structlog.get_logger(__name__)

#: Public, anonymous, requester-pays-free. Azure carries the same data for anyone who prefers it.
S3_BUCKET: Final[str] = "s3://overturemaps-us-west-2/release"
S3_REGION: Final[str] = "us-west-2"

#: Pinned, not "latest". A release is a dated snapshot and every value read from it inherits that
#: date; resolving "latest" at runtime would silently change what a rebuild produces and make two
#: indexes built a week apart impossible to tell apart. Bumping this is a reviewed decision.
DEFAULT_RELEASE: Final[str] = "2026-09-23.1"

#: India, generously. Overture is global and the bbox is the only thing keeping a country-sized
#: extract from being a planet-sized one.
INDIA_BBOX: Final[tuple[float, float, float, float]] = (6.5, 68.0, 36.0, 97.5)

#: Rows per fetch from DuckDB. The query result is streamed rather than materialised: an
#: India-wide extract is millions of rows and `fetchall()` would hold all of them at once.
FETCH_BATCH: Final[int] = 50_000


@dataclass(slots=True)
class OvertureReport:
    """What one extract read, and what it refused. Returned so a caller can assert on it."""

    places: int = 0
    rows_read: int = 0
    not_a_business: int = 0
    #: Contact values dropped for not being that kind of value -- the column-shifted rows.
    bad_phone: int = 0
    bad_email: int = 0
    bad_website: int = 0
    with_phone: int = 0
    with_email: int = 0
    with_website: int = 0
    by_category: dict[str, int] = field(default_factory=dict)

    @property
    def top_categories(self) -> list[tuple[str, int]]:
        return sorted(self.by_category.items(), key=lambda kv: -kv[1])[:10]


def places_url(release: str = DEFAULT_RELEASE) -> str:
    """The object path holding one release's places."""
    return f"{S3_BUCKET}/{release}/theme=places/type=place/*"


def category_counts(
    *,
    bbox: tuple[float, float, float, float] = INDIA_BBOX,
    release: str = DEFAULT_RELEASE,
    connection: Any | None = None,
) -> list[tuple[str, int]]:
    """Every category in use inside `bbox`, with how many places carry it, commonest first.

    Overture publishes no category list as a file, so the vocabulary is read from the data. The
    result is checked in as a seed (`db/seeds/data/overture-categories-in.json`) and the taxonomy
    mapping is validated against it, so a mapping naming a category Overture does not publish
    fails the build instead of silently matching nothing.

    Counted over `taxonomy.hierarchy`, so a place is counted once per level it belongs to. These
    are category mentions, not places: they exist to show what is worth mapping and must never
    rank or bill anything.
    """
    con, source = _connect(release, connection)
    min_lat, min_lng, max_lat, max_lng = bbox
    rows = con.execute(
        f"""
        select cat, count(*) as n from (
          select unnest(taxonomy.hierarchy) as cat
            from {source}
           where bbox.ymin between ? and ? and bbox.xmin between ? and ?
             and names.primary is not null
        ) group by 1 order by n desc
        """,  # noqa: S608 -- `source` is a constant or a fixed test table name
        [min_lat, max_lat, min_lng, max_lng],
    ).fetchall()
    return [(str(c), int(n)) for c, n in rows]


def build_index(
    out: Path,
    *,
    bbox: tuple[float, float, float, float] = INDIA_BBOX,
    release: str = DEFAULT_RELEASE,
    min_confidence: float = 0.0,
    connection: Any | None = None,
) -> OvertureReport:
    """Extracts `bbox` from an Overture release into a place index at `out`.

    Writes to a staging neighbour and renames, so a build killed halfway cannot leave a partial
    index that queries would answer from.

    `connection` exists for tests: they pass a DuckDB handle holding a local table named
    `places_src`, so the mapping is exercised against real DuckDB and real Parquet types without
    a test ever reaching the network (docs/08).
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = out.with_suffix(out.suffix + ".building")
    staging.unlink(missing_ok=True)

    report = OvertureReport()
    started = datetime.now(UTC)
    log.info(
        "overture extract started", release=release, bbox=bbox, out=str(out), staging=str(staging)
    )

    with IndexWriter(staging) as writer:
        writer.add(_places(bbox, release, min_confidence, report, connection))
        writer.finish(extract_date=_release_date(release))

    staging.replace(out)
    log.info(
        "overture extract finished",
        places=report.places,
        rows_read=report.rows_read,
        not_a_business=report.not_a_business,
        bad_phone=report.bad_phone,
        with_phone=report.with_phone,
        with_email=report.with_email,
        seconds=round((datetime.now(UTC) - started).total_seconds(), 1),
        size_mb=round(out.stat().st_size / 1_048_576, 1),
    )
    return report


def _places(
    bbox: tuple[float, float, float, float],
    release: str,
    min_confidence: float,
    report: OvertureReport,
    connection: Any | None,
) -> Iterator[Place]:
    """Streams the rows DuckDB returns, mapping each into a `Place` or counting why not."""
    con, source = _connect(release, connection)
    min_lat, min_lng, max_lat, max_lng = bbox
    url = places_url(release)

    # The bbox predicate is on the `bbox` struct, not the geometry, because DuckDB can push a
    # plain numeric comparison down into Parquet row-group statistics and skip whole files. A
    # spatial predicate on `geometry` cannot be pushed down and reads everything.
    result = con.execute(
        f"""
        select id, names.primary, basic_category, taxonomy.primary, taxonomy.hierarchy,
               (bbox.ymin + bbox.ymax) / 2, (bbox.xmin + bbox.xmax) / 2,
               phones, emails, websites, socials,
               addresses, confidence, operating_status
          from {source}
         where bbox.ymin between ? and ?
           and bbox.xmin between ? and ?
           and names.primary is not null
           and coalesce(confidence, 0) >= ?
        """,  # noqa: S608 -- `source` is a constant or a fixed test table name, never caller data
        [min_lat, max_lat, min_lng, max_lng, min_confidence],
    )

    while True:
        rows = result.fetchmany(FETCH_BATCH)
        if not rows:
            break
        for row in rows:
            report.rows_read += 1
            place = _to_place(row, url, report)
            if place is None:
                continue
            report.places += 1
            report.by_category[place.category] = report.by_category.get(place.category, 0) + 1
            if place.phone:
                report.with_phone += 1
            if place.email:
                report.with_email += 1
            if place.website:
                report.with_website += 1
            yield place
        log.info("overture extract progress", rows_read=report.rows_read, places=report.places)


def _to_place(row: tuple[Any, ...], url: str, report: OvertureReport) -> Place | None:
    (
        place_id,
        name,
        basic_category,
        taxonomy,
        hierarchy,
        lat,
        lon,
        phones,
        emails,
        websites,
        socials,
        addresses,
        confidence,
        operating_status,
    ) = row

    # The leaf is the most specific thing known about this place, and the hierarchy is every
    # level above it. Both are kept: the leaf is what a person reads, the hierarchy is what a
    # search matches. Storing only one of them was measured at 12-16% recall against the
    # taxonomy, against 96.2% for the whole path -- a request for restaurants matches 4,276
    # places by leaf and 11,631 by hierarchy.
    category = taxonomy or basic_category
    categories = tuple(dict.fromkeys(c for c in (hierarchy or []) if c)) or (
        (category,) if category else ()
    )
    # The deny list names mid-level categories (`atm`, `bus_stop`), so it is tested against every
    # level rather than the leaf alone: an `atm` whose leaf is `bank_atm` is still an ATM.
    if not category or any(not is_business(c, operating_status) for c in (category, *categories)):
        report.not_a_business += 1
        return None

    # Each contact value is checked against its own shape. The corrupted rows shift every
    # field one place, so trusting email and website because the phone happened to parse is how
    # a postcode ends up stored as a company's website.
    phone = _checked(_first(phones), looks_like_phone, report, "bad_phone")
    email = _checked(_first(emails), looks_like_email, report, "bad_email")
    website = _checked(_first(websites), looks_like_url, report, "bad_website")

    address = _first_struct(addresses)
    return Place(
        source=OVERTURE,
        external_id=str(place_id),
        name=str(name),
        category=str(category),
        categories=categories,
        lat=float(lat),
        lon=float(lon),
        # Overture publishes no per-record web page, so provenance points at the release object
        # the row came from: not clickable, but exact -- anyone can query this id in this release
        # and see the same values.
        source_url=url,
        phone=phone,
        website=website,
        email=email,
        socials=tuple(s for s in (socials or []) if s),
        street=_get(address, "freeform"),
        city=_get(address, "locality"),
        postcode=_get(address, "postcode"),
        region=_get(address, "region"),
        country=_get(address, "country"),
        confidence=float(confidence) if confidence is not None else None,
        operating_status=operating_status,
    )


def _connect(release: str, connection: Any | None) -> tuple[Any, str]:
    """Returns a DuckDB handle and the relation to read places from."""
    if connection is not None:
        return connection, "places_src"
    # Imported here, not at module scope: a worker that only queries the index should not pay
    # DuckDB's import cost, and a deployment that never builds one need not install it.
    import duckdb  # noqa: PLC0415

    con = duckdb.connect()
    con.execute(f"install httpfs; load httpfs; set s3_region='{S3_REGION}';")
    return con, f"read_parquet('{places_url(release)}', hive_partitioning=1)"


def _checked(
    value: str | None,
    looks_right: "Callable[[str | None], bool]",
    report: OvertureReport,
    counter: str,
) -> str | None:
    """Keeps a value only if it is the kind of value it claims to be, counting what it drops."""
    if value is None:
        return None
    if looks_right(value):
        return value
    setattr(report, counter, getattr(report, counter) + 1)
    return None


def _first(values: Any) -> str | None:
    """The first non-empty entry of a Parquet list column."""
    if not values:
        return None
    for value in values:
        if value and str(value).strip():
            return str(value).strip()
    return None


def _first_struct(values: Any) -> Any:
    if not values:
        return None
    return values[0]


def _get(struct: Any, key: str) -> str | None:
    if struct is None:
        return None
    value = struct.get(key) if isinstance(struct, dict) else getattr(struct, key, None)
    text = str(value).strip() if value is not None else ""
    return text or None


def _release_date(release: str) -> datetime | None:
    """The release's own date, which is the freshness every value from it inherits."""
    try:
        return datetime.strptime(release.split(".", maxsplit=1)[0], "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None
