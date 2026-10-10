"""Fetching an OpenStreetMap extract from Geofabrik.

Geofabrik rebuilds each extract daily and publishes an MD5 beside it. Both matter here: the daily
rebuild is what keeps a file-based source from going stale, and the checksum is what stops a
truncated 1.6 GB download from being parsed into a quietly incomplete index.

Sizes and URLs below were verified against the server on 2026-10-10 rather than copied from a
page; `region_url` is exercised by a test that only checks the shape, because a test must not
call the live site (docs/08).

Attribution is not optional. This data is ODbL 1.0, and anything built from it carries
"data (c) OpenStreetMap contributors, ODbL 1.0" wherever it is shown or exported (ADR-0016).
"""

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Final

import httpx
import structlog

from app.connectors.factory import USER_AGENT

log = structlog.get_logger(__name__)

GEOFABRIK: Final[str] = "https://download.geofabrik.de"

#: Regions this product covers, with the sizes measured on 2026-10-10. A zone is worth choosing
#: over the whole country when disk or RAM is tight: the index of a zone answers queries for that
#: zone exactly as well.
REGIONS: Final[dict[str, str]] = {
    "india": "asia/india",  # 1631 MB
    "india-northern": "asia/india/northern-zone",  # 213 MB
    "india-southern": "asia/india/southern-zone",  # 532 MB
    "india-western": "asia/india/western-zone",  # 211 MB
    "india-central": "asia/india/central-zone",  # 335 MB
    "india-eastern": "asia/india/eastern-zone",  # 236 MB
    "india-north-eastern": "asia/india/north-eastern-zone",  # 105 MB
}

#: Bytes per read. Large enough that a gigabyte does not cost a million iterations.
_CHUNK: Final[int] = 1 << 20

#: How often download progress is logged, in bytes.
_LOG_EVERY: Final[int] = 128 << 20


@dataclass(frozen=True, slots=True)
class Extract:
    """A downloaded extract and when its data is from."""

    path: Path
    #: The server's `Last-Modified`: the day OSM data was cut, which is the freshness every
    #: value read from it inherits.
    published_at: datetime | None
    size_bytes: int
    #: False when the local copy was already current and nothing was transferred.
    downloaded: bool


def region_url(region: str) -> str:
    """The extract URL for a known region name."""
    try:
        return f"{GEOFABRIK}/{REGIONS[region]}-latest.osm.pbf"
    except KeyError:
        known = ", ".join(sorted(REGIONS))
        raise ValueError(f"unknown region {region!r}; known regions: {known}") from None


def fetch_extract(
    region: str,
    dest_dir: Path,
    *,
    force: bool = False,
    timeout: float = 60.0,
    verify_checksum: bool = True,
) -> Extract:
    """Downloads `region`'s extract into `dest_dir`, skipping it if the local copy is current.

    Current means same `Last-Modified` and same length as the server reports. That check costs one
    `HEAD`, where re-downloading costs 1.6 GB, so a daily refresh that finds nothing new is almost
    free and can be run from cron without thought.
    """
    url = region_url(region)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{region}-latest.osm.pbf"

    with httpx.Client(
        follow_redirects=True, timeout=timeout, headers={"User-Agent": USER_AGENT}
    ) as client:
        head = client.head(url)
        head.raise_for_status()
        published_at = _published_at(head.headers.get("last-modified"))
        expected = int(head.headers.get("content-length", 0)) or None

        if not force and _is_current(dest, expected, published_at):
            log.info(
                "osm extract already current",
                region=region,
                path=str(dest),
                published_at=published_at.isoformat() if published_at else None,
            )
            return Extract(
                path=dest,
                published_at=published_at,
                size_bytes=dest.stat().st_size,
                downloaded=False,
            )

        log.info(
            "osm extract downloading",
            region=region,
            url=url,
            expected_mb=round(expected / 1_048_576, 1) if expected else None,
        )
        digest = _stream_to_file(client, url, dest)
        if verify_checksum:
            _verify(client, url, dest, digest)

    if published_at is not None:
        # Keep the file's mtime as the data's date, so an ingest that is handed only a path can
        # still record what the values are actually from.
        stamp = published_at.timestamp()
        os.utime(dest, (stamp, stamp))

    size = dest.stat().st_size
    log.info("osm extract ready", region=region, path=str(dest), size_mb=round(size / 1_048_576, 1))
    return Extract(path=dest, published_at=published_at, size_bytes=size, downloaded=True)


def _stream_to_file(client: httpx.Client, url: str, dest: Path) -> str:
    """Streams `url` to `dest` via a partial file, returning the MD5 of what was written.

    The partial file is what makes a failed download safe: an interrupted transfer leaves
    `*.part` behind and the real path still holds the last good copy, so the next run compares
    against something valid instead of parsing a truncated file.
    """
    part = dest.with_suffix(dest.suffix + ".part")
    part.unlink(missing_ok=True)
    # Not a security hash: it is compared against the MD5 Geofabrik publishes, which is the only
    # checksum on offer.
    md5 = hashlib.md5(usedforsecurity=False)
    written = 0
    next_log = _LOG_EVERY
    with client.stream("GET", url) as response:
        response.raise_for_status()
        with part.open("wb") as handle:
            for chunk in response.iter_bytes(_CHUNK):
                handle.write(chunk)
                md5.update(chunk)
                written += len(chunk)
                if written >= next_log:
                    log.info("osm extract progress", downloaded_mb=written // 1_048_576)
                    next_log += _LOG_EVERY
    part.replace(dest)
    return md5.hexdigest()


def _verify(client: httpx.Client, url: str, dest: Path, digest: str) -> None:
    """Compares the download against Geofabrik's published MD5, deleting it if they disagree."""
    response = client.get(f"{url}.md5")
    if response.status_code != httpx.codes.OK:
        log.warning(
            "osm extract checksum unavailable", url=f"{url}.md5", status=response.status_code
        )
        return
    expected = response.text.split()[0].strip()
    if expected != digest:
        dest.unlink(missing_ok=True)
        raise OSError(
            f"checksum mismatch for {url}: server says {expected}, downloaded file is {digest}"
        )
    log.info("osm extract checksum verified", md5=digest)


def _is_current(dest: Path, expected_size: int | None, published_at: datetime | None) -> bool:
    if not dest.is_file():
        return False
    stat = dest.stat()
    if expected_size is not None and stat.st_size != expected_size:
        return False
    if published_at is None:
        return expected_size is not None
    local = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    # A second of tolerance: `Last-Modified` has whole-second resolution and some filesystems
    # keep mtime with less.
    return abs((local - published_at).total_seconds()) <= 1


def _published_at(header: str | None) -> datetime | None:
    if not header:
        return None
    try:
        parsed = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
