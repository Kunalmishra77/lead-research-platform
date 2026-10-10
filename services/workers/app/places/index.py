"""The place index: a SQLite file holding every named business from an OSM extract.

SQLite on disk rather than rows in Postgres, and the reason is measured rather than stylistic
(ADR-0016). The Supabase free plan gives 500 MB and a lead costs 11.8 KB of it, so the database
holds about forty thousand leads in total. An India-wide POI index is roughly 300 MB. Putting the
abundant data in the scarce store would have spent three fifths of the product's capacity before
a single lead existed, while the VPS it runs on has 118 GB free.

The shape that follows: breadth lives in a file with no quota and no row cost, and Postgres holds
only leads actually being worked on.

An R*Tree index answers the one question discovery asks -- "named businesses of this kind inside
this box" -- in a single index scan. `DiscoveryQuery` already carries a bbox, so no projection or
distance maths is needed anywhere in this module.
"""

import sqlite3
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from app.places.model import Place

#: Bumped when the schema changes in a way that makes an existing file unreadable, which forces
#: a rebuild rather than a confusing query error against the old shape.
SCHEMA_VERSION: Final[int] = 2

SCHEMA: Final[str] = """
create table if not exists places (
  id               integer primary key,
  source           text    not null,
  external_id      text    not null,
  name             text    not null,
  category         text    not null,
  lat              real    not null,
  lon              real    not null,
  source_url       text    not null default '',
  phone            text,
  website          text,
  email            text,
  socials          text,
  street           text,
  housenumber      text,
  city             text,
  postcode         text,
  region           text,
  country          text,
  confidence       real,
  operating_status text,
  updated_at       text
);

-- A record's identity is its dataset plus that dataset's id; the integer primary key exists
-- only to join the R*Tree, which indexes by rowid and nothing else.
create unique index if not exists places_record on places (source, external_id);
create index if not exists places_category on places (category);
-- Discovery filters on both at once, and a sweep of one city asks for one source.
create index if not exists places_source_category on places (source, category);

create virtual table if not exists places_geo using rtree (
  id, min_lat, max_lat, min_lon, max_lon
);

create table if not exists meta (key text primary key, value text not null);
"""

#: Columns in the order `_to_place` reads them.
_COLUMNS: Final[str] = (
    "source, external_id, name, category, lat, lon, source_url, "
    "phone, website, email, socials, street, housenumber, city, postcode, "
    "region, country, confidence, operating_status, updated_at"
)

#: How many columns `_COLUMNS` names, for the insert's placeholder run.
_COLUMN_COUNT: Final[int] = len(_COLUMNS.split(","))

#: Several social URLs share one column. A newline, because it cannot occur inside a URL --
#: a comma or a semicolon can, and would split one link into two unusable halves.
SOCIALS_SEPARATOR: Final[str] = "\n"


@dataclass(frozen=True, slots=True)
class IndexStats:
    """What an index holds, for the health check and the freshness a lead is shown with."""

    places: int
    built_at: datetime | None
    #: `Last-Modified` of the extract this was built from: when the data is from, not when we
    #: read it.
    extract_date: datetime | None
    schema_version: int


class PlaceIndex:
    """Read-only access to a built index. Safe to share; one connection per calling thread.

    Queries are synchronous because SQLite is. A caller inside the event loop wraps them in
    `asyncio.to_thread`, which is cheap next to the work itself: these are index scans against a
    local file, not network calls.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._local = threading.local()

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.is_file()

    def _conn(self) -> sqlite3.Connection:
        # One connection per thread rather than one shared under a lock: `asyncio.to_thread`
        # uses a bounded pool, so this stays a handful of read-only handles, and read-only
        # SQLite connections do not contend.
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def search(
        self,
        *,
        bbox: tuple[float, float, float, float],
        categories: Sequence[str] = (),
        source: str | None = None,
        name: str | None = None,
        contactable: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Place]:
        """Named businesses inside `bbox`, optionally of certain categories.

        `bbox` is (min_lat, min_lng, max_lat, max_lng), matching `DiscoveryQuery.bbox`.

        Ordered by id so that paging through a large area with `offset` is stable. Ordering by
        anything derived from the data -- distance from a centre, say -- would reshuffle the rows
        whenever the index is rebuilt and make page two skip or repeat businesses.
        """
        where, params = _bbox_where(bbox)
        if categories:
            where.append(f"p.category in ({', '.join('?' * len(categories))})")
            params.extend(categories)
        if source:
            where.append("p.source = ?")
            params.append(source)
        if contactable:
            # The product's unit is a business someone can be reached at, so this is a first
            # class filter rather than something every caller re-derives and gets subtly wrong.
            where.append("(p.phone is not null or p.email is not null)")
        if name:
            where.append("p.name like ? escape '\\'")
            params.append(f"%{_escape_like(name)}%")
        params.extend((limit, offset))
        sql = (
            f"select {_prefixed(_COLUMNS, 'p')} from places_geo g "  # noqa: S608 -- see below
            f"join places p on p.id = g.id "
            f"where {' and '.join(where)} "
            f"order by p.id limit ? offset ?"
        )
        # S608 (SQL built from a string) is suppressed because nothing interpolated is caller
        # data: the only pieces are a fixed column list and a run of `?` placeholders counted
        # from `categories`. Every value travels as a bound parameter.
        rows = self._conn().execute(sql, params).fetchall()
        return [_to_place(row) for row in rows]

    def count(
        self,
        *,
        bbox: tuple[float, float, float, float],
        categories: Sequence[str] = (),
        source: str | None = None,
        contactable: bool = False,
    ) -> int:
        """How many businesses an area holds, for planning a sweep before running one."""
        where, params = _bbox_where(bbox)
        if source:
            where.append("p.source = ?")
            params.append(source)
        if contactable:
            where.append("(p.phone is not null or p.email is not null)")
        if categories:
            where.append(f"p.category in ({', '.join('?' * len(categories))})")
            params.extend(categories)
        sql = (
            "select count(*) as n from places_geo g join places p on p.id = g.id "  # noqa: S608
            f"where {' and '.join(where)}"
        )
        row = self._conn().execute(sql, params).fetchone()
        return int(row["n"]) if row else 0

    def stats(self) -> IndexStats:
        conn = self._conn()
        places = int(conn.execute("select count(*) as n from places").fetchone()["n"])
        meta = {r["key"]: r["value"] for r in conn.execute("select key, value from meta")}
        return IndexStats(
            places=places,
            built_at=_parse_time(meta.get("built_at")),
            extract_date=_parse_time(meta.get("extract_date")),
            schema_version=int(meta.get("schema_version", 0)),
        )

    def close(self) -> None:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


class IndexWriter:
    """Builds an index. Used by the ingest pass and by tests; not used while serving."""

    #: Rows per `executemany`. Large enough that the per-statement overhead disappears, small
    #: enough that a failed build does not have gigabytes of uncommitted work to roll back.
    BATCH: Final[int] = 10_000

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        # A build is a single-writer bulk load into a file nobody is reading yet, so durability
        # per transaction buys nothing: if it fails, the file is discarded and rebuilt.
        self._conn.execute("pragma journal_mode = off")
        self._conn.execute("pragma synchronous = off")
        self._conn.executescript(SCHEMA)

    def add(self, places: Iterable[Place]) -> int:
        """Writes places in batches. Returns how many rows were written."""
        written = 0
        batch: list[Place] = []
        for place in places:
            batch.append(place)
            if len(batch) >= self.BATCH:
                written += self._flush(batch)
                batch.clear()
        written += self._flush(batch)
        return written

    def _flush(self, batch: Sequence[Place]) -> int:
        if not batch:
            return 0
        self._conn.executemany(
            f"insert or ignore into places ({_COLUMNS}) "  # noqa: S608
            f"values ({', '.join('?' * _COLUMN_COUNT)})",
            [
                (
                    p.source,
                    p.external_id,
                    p.name,
                    p.category,
                    p.lat,
                    p.lon,
                    p.source_url,
                    p.phone,
                    p.website,
                    p.email,
                    SOCIALS_SEPARATOR.join(p.socials) if p.socials else None,
                    p.street,
                    p.housenumber,
                    p.city,
                    p.postcode,
                    p.region,
                    p.country,
                    p.confidence,
                    p.operating_status,
                    p.updated_at.isoformat() if p.updated_at else None,
                )
                for p in batch
            ],
        )
        self._conn.commit()
        return len(batch)

    def finish(self, *, extract_date: datetime | None = None) -> None:
        """Fills the R*Tree from the rows written, records provenance, and compacts.

        The R*Tree is filled at the end rather than alongside each insert: building a spatial
        index incrementally re-balances it continuously, where one pass over finished rows does
        that work once.
        """
        self._conn.execute(
            "insert into places_geo (id, min_lat, max_lat, min_lon, max_lon) "
            "select id, lat, lat, lon, lon from places"
        )
        for key, value in (
            ("schema_version", str(SCHEMA_VERSION)),
            ("built_at", datetime.now(UTC).isoformat()),
            ("extract_date", extract_date.isoformat() if extract_date else ""),
        ):
            self._conn.execute(
                "insert into meta (key, value) values (?, ?) "
                "on conflict (key) do update set value = excluded.value",
                (key, value),
            )
        self._conn.commit()
        self._conn.execute("analyze")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "IndexWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _bbox_where(bbox: tuple[float, float, float, float]) -> tuple[list[str], list[object]]:
    """The R*Tree clause for "overlaps this box", shared so `search` and `count` cannot disagree.

    Overlap (`max >= query_min and min <= query_max`), not containment. SQLite's R*Tree keeps
    coordinates as 32-bit floats and rounds each stored box *outward* so that queries can never
    miss an entry. A point at latitude 28.45 is therefore stored as roughly
    [28.449999, 28.450001], and the containment form this started as -- `min >= ? and max <= ?`
    -- dropped it from a box ending at exactly 28.45. A test on the boundary caught it.

    The cost of overlap is a false positive within one float's rounding of the edge, under a
    metre. The cost of containment was silently losing businesses on every boundary, which for a
    tiled sweep is one missing stripe per tile edge.
    """
    min_lat, min_lng, max_lat, max_lng = bbox
    return (
        ["g.max_lat >= ?", "g.min_lat <= ?", "g.max_lon >= ?", "g.min_lon <= ?"],
        [min_lat, max_lat, min_lng, max_lng],
    )


def _prefixed(columns: str, alias: str) -> str:
    return ", ".join(f"{alias}.{c.strip()}" for c in columns.split(","))


def _escape_like(term: str) -> str:
    """Escapes LIKE wildcards so a name containing `%` matches literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_place(row: sqlite3.Row) -> Place:
    socials = row["socials"]
    return Place(
        source=row["source"],
        external_id=row["external_id"],
        name=row["name"],
        category=row["category"],
        lat=float(row["lat"]),
        lon=float(row["lon"]),
        source_url=row["source_url"] or "",
        phone=row["phone"],
        website=row["website"],
        email=row["email"],
        socials=tuple(socials.split(SOCIALS_SEPARATOR)) if socials else (),
        street=row["street"],
        housenumber=row["housenumber"],
        city=row["city"],
        postcode=row["postcode"],
        region=row["region"],
        country=row["country"],
        confidence=row["confidence"],
        operating_status=row["operating_status"],
        updated_at=_parse_time(row["updated_at"]),
    )


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
