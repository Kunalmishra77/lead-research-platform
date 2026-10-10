"""Building the place index from the command line.

    uv run python -m app.places build-overture                 # the discovery source
    uv run python -m app.places build-osm --region india       # the second opinion
    uv run python -m app.places stats

An operator tool, not a job. It runs on the machine that holds the index -- monthly, when each
dataset publishes -- and the workers only ever read what it produced. Keeping it out of the job
framework is deliberate: a multi-gigabyte extract and a multi-minute parse have nothing in common
with a research task and should not share its retry, budget or cancellation machinery.
"""

import argparse
import sys
from pathlib import Path

import structlog

from app.logging import configure_logging
from app.places.geofabrik import REGIONS, fetch_extract
from app.places.index import PlaceIndex
from app.places.osm import DEFAULT_LOCATION_STORAGE
from app.places.osm import build_index as build_osm_index
from app.places.overture import DEFAULT_RELEASE, INDIA_BBOX
from app.places.overture import build_index as build_overture_index

log = structlog.get_logger("leadforge.places")

#: Where extracts and indexes live. A directory, because they belong together and the deployment
#: mounts one volume for both -- without a persistent volume every redeploy re-downloads
#: gigabytes and rebuilds for nothing (ADR-0016).
DEFAULT_DATA_DIR = Path("data/places")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.places", description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"where extracts and indexes live (default: {DEFAULT_DATA_DIR})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    overture = sub.add_parser("build-overture", help="extract Overture Places into the index")
    overture.add_argument("--release", default=DEFAULT_RELEASE)
    overture.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        default=list(INDIA_BBOX),
        metavar=("MIN_LAT", "MIN_LNG", "MAX_LAT", "MAX_LNG"),
        help="area to extract (default: India)",
    )
    overture.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        help="drop rows the dataset is less sure of than this (0-1)",
    )

    osm = sub.add_parser("build-osm", help="download an OSM extract and add it to the index")
    osm.add_argument("--region", default="india", choices=sorted(REGIONS))
    osm.add_argument("--force-download", action="store_true")
    osm.add_argument(
        "--location-storage",
        default=DEFAULT_LOCATION_STORAGE,
        choices=["flex_mem", "sparse_file_array", "dense_file_array", "sparse_mem_array"],
        help="node-location cache; use sparse_file_array when memory is tight",
    )
    osm.add_argument("--skip-checksum", action="store_true")

    sub.add_parser("stats", help="what the built indexes hold")

    args = parser.parse_args(argv)
    configure_logging()

    if args.command == "build-overture":
        return _build_overture(args)
    if args.command == "build-osm":
        return _build_osm(args)
    return _stats(args)


def _build_overture(args: argparse.Namespace) -> int:
    report = build_overture_index(
        args.data_dir / "overture.sqlite",
        bbox=tuple(args.bbox),
        release=args.release,
        min_confidence=args.min_confidence,
    )
    _report(report.places, report.top_categories)
    if report.places == 0:
        log.error("overture index is empty; the bbox or the release is wrong")
        return 1
    log.info(
        "overture reachability",
        places=report.places,
        with_phone=report.with_phone,
        with_email=report.with_email,
        with_website=report.with_website,
        refused_not_a_business=report.not_a_business,
        refused_bad_phone=report.bad_phone,
    )
    return 0


def _build_osm(args: argparse.Namespace) -> int:
    extract = fetch_extract(
        args.region,
        args.data_dir,
        force=args.force_download,
        verify_checksum=not args.skip_checksum,
    )
    report = build_osm_index(
        extract.path,
        args.data_dir / f"osm-{args.region}.sqlite",
        location_storage=args.location_storage,
        extract_date=extract.published_at,
    )
    _report(report.places, report.top_categories)
    return 1 if report.places == 0 else 0


def _report(places: int, categories: list[tuple[str, int]]) -> None:
    log.info("index built", places=places)
    for category, count in categories:
        log.info("category", category=category, places=count)


def _stats(args: argparse.Namespace) -> int:
    found = sorted(args.data_dir.glob("*.sqlite"))
    if not found:
        log.error("no index found", data_dir=str(args.data_dir))
        return 1
    for path in found:
        stats = PlaceIndex(path).stats()
        log.info(
            "index",
            path=str(path),
            places=stats.places,
            extract_date=stats.extract_date.date().isoformat() if stats.extract_date else None,
            built_at=stats.built_at.isoformat() if stats.built_at else None,
            size_mb=round(path.stat().st_size / 1_048_576, 1),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
