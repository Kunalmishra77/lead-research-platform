"""Building the place index from the command line.

    uv run python -m app.osm build --region india
    uv run python -m app.osm stats

This is an operator tool, not part of a job. It runs on the machine that holds the extract --
monthly, or whenever Geofabrik's daily rebuild is worth picking up -- and the workers only ever
read what it produced. Keeping it out of the job framework is deliberate: a 1.6 GB download and a
multi-minute parse have nothing in common with a research task and should not share its retry,
budget or cancellation machinery.
"""

import argparse
import sys
from pathlib import Path

import structlog

from app.logging import configure_logging
from app.osm.download import REGIONS, fetch_extract
from app.osm.index import OsmIndex
from app.osm.ingest import DEFAULT_LOCATION_STORAGE, build_index

log = structlog.get_logger("leadforge.osm")

#: Where the extract and the index live. A directory, because the two belong together and the
#: deployment mounts one volume for both (ADR-0016: Coolify needs a persistent volume here, or
#: every redeploy re-downloads the extract).
DEFAULT_DATA_DIR = Path("data/osm")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.osm", description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"where the extract and index live (default: {DEFAULT_DATA_DIR})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="download an extract and build the index")
    build.add_argument("--region", default="india", choices=sorted(REGIONS))
    build.add_argument(
        "--force-download",
        action="store_true",
        help="re-download even if the local copy is current",
    )
    build.add_argument(
        "--location-storage",
        default=DEFAULT_LOCATION_STORAGE,
        choices=["flex_mem", "sparse_file_array", "dense_file_array", "sparse_mem_array"],
        help="node-location cache; use sparse_file_array when memory is tight",
    )
    build.add_argument(
        "--skip-checksum", action="store_true", help="do not verify the published MD5"
    )

    sub.add_parser("stats", help="what the built index holds")

    args = parser.parse_args(argv)
    configure_logging()

    if args.command == "build":
        return _build(args)
    return _stats(args)


def _build(args: argparse.Namespace) -> int:
    extract = fetch_extract(
        args.region,
        args.data_dir,
        force=args.force_download,
        verify_checksum=not args.skip_checksum,
    )
    report = build_index(
        extract.path,
        _index_path(args.data_dir, args.region),
        location_storage=args.location_storage,
        extract_date=extract.published_at,
    )
    log.info("osm index built", region=args.region, places=report.places)
    for category, count in report.top_categories:
        log.info("osm category", category=category, places=count)
    if report.places == 0:
        log.error("osm index is empty; the extract or the tag rules are wrong")
        return 1
    return 0


def _stats(args: argparse.Namespace) -> int:
    found = sorted(args.data_dir.glob("*.sqlite"))
    if not found:
        log.error("no index found", data_dir=str(args.data_dir))
        return 1
    for path in found:
        stats = OsmIndex(path).stats()
        log.info(
            "osm index",
            path=str(path),
            places=stats.places,
            extract_date=stats.extract_date.date().isoformat() if stats.extract_date else None,
            built_at=stats.built_at.isoformat() if stats.built_at else None,
            size_mb=round(path.stat().st_size / 1_048_576, 1),
        )
    return 0


def _index_path(data_dir: Path, region: str) -> Path:
    return data_dir / f"{region}.sqlite"


if __name__ == "__main__":
    sys.exit(main())
