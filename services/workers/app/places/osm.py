"""Reading an `.osm.pbf` extract into a place index.

One streaming pass. `FileProcessor` yields nodes before ways in file order, and `with_locations()`
caches every node position as it goes, so a way's corners are known by the time the way arrives.
A `KeyFilter` on the primary tags skips the overwhelming majority of elements -- an India extract
is mostly untagged geometry -- without disturbing that cache; both filter orders were checked
against a real `.pbf` and located 5 of 5 way nodes either way.

Relations are not read. Measured on Delhi, POIs are 18,185 nodes and 4,312 ways; relations are a
small remainder and reading them means assembling multipolygons, which costs far more than the
few businesses it would add. `IngestReport.relations_skipped` counts what that leaves on the
table so the decision stays visible rather than becoming folklore.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import osmium
import structlog

from app.places.index import IndexWriter
from app.places.model import PRIMARY_KEYS, Place, osm_place

log = structlog.get_logger(__name__)

#: How often progress is logged, in elements that passed the tag filter.
_LOG_EVERY: Final[int] = 25_000

#: Node-location cache. `flex_mem` keeps positions in RAM and adapts to the id range; an India
#: extract needs roughly a gigabyte, against the 7.6 GB free on the VPS that runs this.
#: `sparse_file_array` is the fallback where memory is tighter and time is not.
DEFAULT_LOCATION_STORAGE: Final[str] = "flex_mem"


@dataclass(slots=True)
class IngestReport:
    """What one build read and wrote. Returned rather than logged so a caller can assert on it."""

    places: int = 0
    nodes: int = 0
    ways: int = 0
    #: Passed the tag filter but was not a named business -- no name, or a denied category.
    not_a_business: int = 0
    #: Ways whose corners were not all in the extract, so no centroid could be computed. Normal
    #: near an extract's edge, where a building continues past the cut.
    ways_without_location: int = 0
    relations_skipped: int = 0
    by_category: dict[str, int] = field(default_factory=dict)

    @property
    def top_categories(self) -> list[tuple[str, int]]:
        return sorted(self.by_category.items(), key=lambda kv: -kv[1])[:10]


def build_index(
    pbf: Path,
    out: Path,
    *,
    location_storage: str = DEFAULT_LOCATION_STORAGE,
    extract_date: datetime | None = None,
) -> IngestReport:
    """Builds the index at `out` from the extract at `pbf`. Overwrites `out` if it exists.

    Writes to a temporary neighbour and renames at the end, so a crashed or killed build cannot
    leave a half-filled index that queries would answer from. On POSIX the rename is atomic; on
    Windows the destination is removed first, which is the same guarantee for a file nothing is
    reading yet.
    """
    if not pbf.is_file():
        raise FileNotFoundError(f"no extract at {pbf}")

    out.parent.mkdir(parents=True, exist_ok=True)
    staging = out.with_suffix(out.suffix + ".building")
    staging.unlink(missing_ok=True)

    report = IngestReport()
    started = datetime.now(UTC)
    log.info("osm ingest started", extract=str(pbf), out=str(out), storage=location_storage)

    with IndexWriter(staging) as writer:
        writer.add(_places(pbf, location_storage, report))
        writer.finish(extract_date=extract_date or _file_date(pbf))

    staging.replace(out)
    log.info(
        "osm ingest finished",
        places=report.places,
        nodes=report.nodes,
        ways=report.ways,
        not_a_business=report.not_a_business,
        ways_without_location=report.ways_without_location,
        relations_skipped=report.relations_skipped,
        seconds=round((datetime.now(UTC) - started).total_seconds(), 1),
        out=str(out),
        size_mb=round(out.stat().st_size / 1_048_576, 1),
    )
    return report


def _places(pbf: Path, location_storage: str, report: IngestReport) -> Iterator[Place]:
    """Yields every named business in the extract, counting what was passed over and why."""
    processor = (
        osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations(location_storage)
        .with_filter(osmium.filter.KeyFilter(*PRIMARY_KEYS))
    )
    seen = 0
    for element in processor:
        seen += 1
        if seen % _LOG_EVERY == 0:
            log.info("osm ingest progress", tagged_elements=seen, places=report.places)

        # isinstance rather than `is_node()` / `is_way()`: the iterator's element type is a union
        # of every OSM entity, and only isinstance narrows it for the type checker. The predicate
        # methods read the same way but leave every attribute access below unchecked.
        if isinstance(element, osmium.osm.Node):
            report.nodes += 1
            place = osm_place(
                osm_type="n",
                osm_id=element.id,
                tags=dict(element.tags),
                lat=element.location.lat,
                lon=element.location.lon,
                updated_at=_timestamp(element),
            )
        elif isinstance(element, osmium.osm.Way):
            report.ways += 1
            centre = _centroid(element)
            if centre is None:
                report.ways_without_location += 1
                continue
            place = osm_place(
                osm_type="w",
                osm_id=element.id,
                tags=dict(element.tags),
                lat=centre[0],
                lon=centre[1],
                updated_at=_timestamp(element),
            )
        else:
            report.relations_skipped += 1
            continue

        if place is None:
            report.not_a_business += 1
            continue
        report.places += 1
        report.by_category[place.category] = report.by_category.get(place.category, 0) + 1
        yield place


def _centroid(way: osmium.osm.Way) -> tuple[float, float] | None:
    """The average of a way's corners, or None if none of them are in the extract.

    A closed way repeats its first node as its last. Averaging the list as given therefore
    double-weights that corner and pulls the pin off centre -- on a test square it landed at
    28.504 where the centre is 28.505. The repeat is dropped before averaging.
    """
    refs = list(way.nodes)
    if len(refs) > 1 and refs[0].ref == refs[-1].ref:
        refs = refs[:-1]
    points = [(n.location.lat, n.location.lon) for n in refs if n.location.valid()]
    if not points:
        return None
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )


def _timestamp(element: osmium.osm.Node | osmium.osm.Way) -> datetime | None:
    """The element's last-edited time, or None when the extract carries no metadata."""
    stamp = element.timestamp
    # osmium returns a zero-valued datetime rather than None for an element with no timestamp,
    # which would otherwise be stored as a real observation from the year 1970.
    if stamp is None or stamp.year <= 1970:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _file_date(pbf: Path) -> datetime:
    """When the extract file was produced, as a stand-in when the caller knows no better."""
    return datetime.fromtimestamp(pbf.stat().st_mtime, tz=UTC)
