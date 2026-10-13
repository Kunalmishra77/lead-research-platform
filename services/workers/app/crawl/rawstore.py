"""Where a crawled page's bytes go (task 3.6, docs/06 §4.9, ADR-0019).

The key is the one docs/06 defines -- `raw/{yyyy}/{mm}/{url_hash}/{fetched_at}.html.gz` -- so the
layout does not depend on which store is behind it. A month is a directory, which is what makes
the 90-day retention sweep a listing rather than a query.

The store is local disk rather than object storage, and ADR-0019 has the measurement: a page is
30 KB gzipped at the median, a crawl fetches 0.58 pages, 38% of leads have a site, so a lakh of
leads is about 660 MB against the 1 GB Supabase Storage free plan -- while the worker host has
134 GB free. These objects are a 90-day cache of public web pages: re-fetchable, not customer
data, and nothing depends on them being there. Losing the volume costs a re-crawl.

`RawStore` is a Protocol so that a paid object store is an addition rather than a rewrite. Only
the local one is written, because an untested second implementation is not a fallback, it is
dead code that will not work the day it is needed.
"""

import gzip
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Protocol

#: gzip level 6. Measured on 12 real pages from the graph: 161 KB of HTML becomes 30 KB at the
#: median, and level 9 saved under 4% more for about three times the CPU.
_GZIP_LEVEL: Final[int] = 6


def storage_key(url_hash: str, fetched_at: datetime) -> str:
    """The key docs/06 §4.9 defines, `raw/{yyyy}/{mm}/{url_hash}/{fetched_at}.html.gz`.

    `url_hash` is the hex digest, so the same URL's fetches sit together and a re-crawl of one
    site is one directory. The timestamp is the basename, so two fetches of the same URL in one
    month never collide, and it is ISO with the colons removed because a colon is not a filename
    on Windows -- which is where the tests run.
    """
    stamp = fetched_at.strftime("%Y%m%dT%H%M%S%fZ")
    return f"raw/{fetched_at:%Y}/{fetched_at:%m}/{url_hash}/{stamp}.html.gz"


class RawStore(Protocol):
    """Somewhere to put a page's bytes and take them back out."""

    async def put(self, key: str, body: bytes) -> int:
        """Stores `body` gzipped under `key`, returning the number of bytes written."""
        ...

    async def get(self, key: str) -> bytes | None:
        """The body as it was fetched, or None when the object is gone (retention, a lost disk)."""
        ...

    async def delete(self, key: str) -> bool:
        """Removes the object. True when there was one."""
        ...


class LocalRawStore:
    """A directory on the worker host's own disk, one named volume (ADR-0019).

    Written and read synchronously. A page is 30 KB and the crawl that produced it already waited
    seconds for politeness, so moving this to a thread would add a context switch to hide a
    sub-millisecond write.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, key: str) -> Path:
        # Resolved and checked against the root: `key` is built here, never taken from a page,
        # but a store that can be talked into writing outside its own directory is a store that
        # one day will be.
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root.resolve()):
            raise ValueError(f"key escapes the raw store root: {key!r}")
        return path

    async def put(self, key: str, body: bytes) -> int:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        packed = gzip.compress(body, _GZIP_LEVEL)
        # Written beside and renamed, so a reader never sees half a file and a crash leaves a
        # `.part` to clean up rather than a truncated page that looks complete.
        scratch = path.with_suffix(path.suffix + ".part")
        scratch.write_bytes(packed)
        scratch.replace(path)
        return len(packed)

    async def get(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return gzip.decompress(path.read_bytes())

    async def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.is_file():
            return False
        path.unlink()
        # The directory for this URL, and then its month, if nothing else is in them. Left behind
        # they would make the retention sweep walk a million empty directories a year from now.
        for parent in (path.parent, path.parent.parent):
            try:
                parent.rmdir()
            except OSError:
                break
        return True


#: docs/04: bodies for 90 days. The row outlives them by nine months, which is why a reader
#: asking for a pruned page gets `None` rather than an error.
RETENTION_DAYS: Final[int] = 90


@dataclass(frozen=True, slots=True)
class Pruned:
    """What one sweep removed."""

    files: int
    bytes: int
    months_removed: int


def prune(root: Path, *, older_than_days: int = RETENTION_DAYS) -> Pruned:
    """Deletes stored bodies older than the retention window (docs/04, ADR-0019).

    By file time rather than by month directory. Dropping whole months would be cheaper and
    would keep a page from the first of a month for up to 120 days, and these pages carry
    people's names and addresses from team and contact pages -- docs/10 treats that as personal
    data, so "roughly ninety days" is not the same promise as ninety days.

    The month directories are still what makes it cheap: a month entirely inside the window is
    skipped without listing it, and one entirely outside it is removed whole.

    Synchronous, and called from a worker's startup rather than inside a request: it walks a
    directory tree, which is the one thing that must not happen on the event loop while a crawl
    is waiting.
    """
    if not root.is_dir():
        return Pruned(0, 0, 0)

    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    files = total = months = 0

    for month_dir in sorted(root.glob("raw/*/*")):
        if not month_dir.is_dir():
            continue
        month = _month_of(month_dir)
        # A month that cannot even have started before the cutoff is entirely inside the
        # window: skipped without listing a single file.
        if month is not None and month > cutoff:
            continue
        for page in month_dir.rglob("*.html.gz"):
            if datetime.fromtimestamp(page.stat().st_mtime, UTC) >= cutoff:
                continue
            total += page.stat().st_size
            page.unlink()
            files += 1
        for url_dir in list(month_dir.iterdir()):
            if url_dir.is_dir() and not any(url_dir.iterdir()):
                url_dir.rmdir()
        if not any(month_dir.iterdir()):
            month_dir.rmdir()
            months += 1

    return Pruned(files=files, bytes=total, months_removed=months)


def _month_of(month_dir: Path) -> datetime | None:
    """The first instant of the month this directory holds, or None if it is not one of ours.

    None rather than an exception: a stray directory under `raw/` is somebody's mistake and the
    sweep's job is to remove old pages, not to refuse to run.
    """
    try:
        return datetime(int(month_dir.parent.name), int(month_dir.name), 1, tzinfo=UTC)
    except ValueError:
        return None
