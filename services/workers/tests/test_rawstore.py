"""The byte store for crawled pages (task 3.6, docs/06 §4.9, ADR-0019).

A real directory in `tmp_path`, not a mock: what is most likely to be wrong is the key, the
rename and the escape check, and a mock of a filesystem agrees with whatever the code does.
"""

import gzip
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.crawl.rawstore import LocalRawStore, Pruned, prune, storage_key, writable

FETCHED = datetime(2026, 10, 13, 14, 25, 36, 123456, tzinfo=UTC)
DIGEST = "a" * 64


def _leftovers(root: Path) -> list[Path]:
    """Any half-written file. A plain function because walking a tree is blocking, and ruff is
    right to say so inside an `async def`."""
    return list(root.rglob("*.part"))


def test_the_key_is_the_one_the_spec_defines() -> None:
    """docs/06 §4.9: `raw/{yyyy}/{mm}/{url_hash}/{fetched_at}.html.gz`.

    A month is a directory, which is what makes the 90-day retention sweep a listing rather
    than a query, and a URL is a directory, so re-reading one site is one place on disk.
    """
    key = storage_key(DIGEST, FETCHED)
    assert key == f"raw/2026/10/{DIGEST}/20261013T142536123456Z.html.gz"


def test_the_timestamp_in_the_key_has_no_colons() -> None:
    """A colon is not a filename on Windows, which is where these tests run and where a
    developer reads the store. ISO with the separators removed keeps it sortable."""
    assert ":" not in storage_key(DIGEST, FETCHED)


def test_two_fetches_of_one_url_in_a_month_do_not_collide() -> None:
    first = storage_key(DIGEST, FETCHED)
    second = storage_key(DIGEST, FETCHED.replace(microsecond=999999))
    assert first != second
    assert Path(first).parent == Path(second).parent


async def test_a_body_comes_back_exactly_as_it_went_in(tmp_path: Path) -> None:
    store = LocalRawStore(tmp_path)
    body = b"<html><body>Dr Sharma's clinic \xe2\x80\x94 98765 43210</body></html>"

    written = await store.put("raw/2026/10/x/one.html.gz", body)

    assert await store.get("raw/2026/10/x/one.html.gz") == body
    assert written < len(body) or len(body) < 200, "a page should compress"


async def test_what_is_on_disk_is_gzip(tmp_path: Path) -> None:
    """Not an implementation detail: the retention sweep, a developer and any future
    re-extraction all read these files without going through this class."""
    store = LocalRawStore(tmp_path)
    await store.put("raw/2026/10/x/one.html.gz", b"<html>hello</html>")

    raw = (tmp_path / "raw/2026/10/x/one.html.gz").read_bytes()
    assert raw[:2] == b"\x1f\x8b"
    assert gzip.decompress(raw) == b"<html>hello</html>"


async def test_a_missing_object_is_absent_rather_than_an_error(tmp_path: Path) -> None:
    """Retention deletes bodies at 90 days and the row outlives them by nine months, so a
    reader asking for a pruned page is normal and must not raise."""
    assert await LocalRawStore(tmp_path).get("raw/2026/01/gone/one.html.gz") is None


async def test_nothing_half_written_is_ever_visible(tmp_path: Path) -> None:
    """Written beside and renamed. A truncated page that looks complete is worse than a missing
    one: the missing one is re-fetched and the truncated one is extracted from."""
    store = LocalRawStore(tmp_path)
    await store.put("raw/2026/10/x/one.html.gz", b"<html>hello</html>")

    assert _leftovers(tmp_path) == []


async def test_deleting_takes_the_empty_directories_with_it(tmp_path: Path) -> None:
    """A million empty directories a year from now would make the retention sweep walk them
    all. The month directory goes only when it is empty."""
    store = LocalRawStore(tmp_path)
    await store.put("raw/2026/10/only/one.html.gz", b"<html>a</html>")
    await store.put("raw/2026/10/other/one.html.gz", b"<html>b</html>")

    assert await store.delete("raw/2026/10/only/one.html.gz") is True

    assert not (tmp_path / "raw/2026/10/only").exists()
    assert (tmp_path / "raw/2026/10/other").exists(), "a sibling still has a page"
    assert (tmp_path / "raw/2026/10").exists(), "the month is not empty"


async def test_deleting_what_is_not_there_says_so(tmp_path: Path) -> None:
    assert await LocalRawStore(tmp_path).delete("raw/2026/10/x/none.html.gz") is False


@pytest.mark.parametrize(
    "key",
    ["../escape.gz", "raw/../../escape.gz", "raw/2026/../../../etc/passwd"],
)
async def test_a_key_cannot_escape_the_store(tmp_path: Path, key: str) -> None:
    """The key is built in this module and never taken from a page, so this cannot happen
    today. It is checked because a store that can be talked into writing outside its own
    directory is one that eventually will be -- the next caller is the one that passes a URL
    through by mistake.
    """
    store = LocalRawStore(tmp_path / "store")
    (tmp_path / "store").mkdir()
    with pytest.raises(ValueError, match="escapes"):
        await store.put(key, b"x")


async def test_an_empty_body_is_stored_rather_than_refused(tmp_path: Path) -> None:
    """A 200 with no body is a real answer from a real site, and the row recording it should
    point at something rather than claim nothing was returned."""
    store = LocalRawStore(tmp_path)
    await store.put("raw/2026/10/x/empty.html.gz", b"")
    assert await store.get("raw/2026/10/x/empty.html.gz") == b""


def _age(path: Path, days: int) -> None:
    """Backdates a file, so the sweep sees a real mtime rather than a patched clock."""
    when = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    os.utime(path, (when, when))


async def _page(store: LocalRawStore, year: int, month: int, name: str, days: int) -> Path:
    key = f"raw/{year:04d}/{month:02d}/{name}/{name}.html.gz"
    await store.put(key, b"<html>page</html>")
    path = store.root / key
    _age(path, days)
    return path


async def test_a_page_inside_the_window_is_kept(tmp_path: Path) -> None:
    store = LocalRawStore(tmp_path)
    now = datetime.now(UTC)
    page = await _page(store, now.year, now.month, "fresh", days=10)

    result = prune(tmp_path)

    assert result.files == 0
    assert page.is_file()


async def test_a_page_past_the_window_is_deleted(tmp_path: Path) -> None:
    """docs/04: bodies for 90 days. The row outlives them by nine months, which is why a
    reader asking for a pruned page gets None rather than an error."""
    store = LocalRawStore(tmp_path)
    old = datetime.now(UTC) - timedelta(days=200)
    page = await _page(store, old.year, old.month, "ancient", days=200)

    result = prune(tmp_path)

    assert result.files == 1
    assert result.bytes > 0
    assert not page.exists()
    key = f"raw/{old.year:04d}/{old.month:02d}/ancient/ancient.html.gz"
    assert await store.get(key) is None


async def test_retention_is_by_file_time_not_by_month(tmp_path: Path) -> None:
    """Dropping whole month directories would be cheaper and would keep a page from the first
    of a month for up to 120 days. These pages carry people's names and addresses from team and
    contact pages, which docs/10 treats as personal data, so "roughly ninety days" is not the
    same promise as ninety days.
    """
    store = LocalRawStore(tmp_path)
    old = datetime.now(UTC) - timedelta(days=100)
    stale = await _page(store, old.year, old.month, "stale", days=100)
    # The same month directory, but fetched inside the window -- a site re-crawled recently.
    recent = await _page(store, old.year, old.month, "recent", days=5)

    result = prune(tmp_path)

    assert result.files == 1
    assert not stale.exists()
    assert recent.is_file(), "a recent page in an old month's directory survives"


async def test_an_emptied_month_is_removed(tmp_path: Path) -> None:
    """Left behind, a million empty directories a year from now would make the next sweep walk
    them all."""
    store = LocalRawStore(tmp_path)
    old = datetime.now(UTC) - timedelta(days=200)
    await _page(store, old.year, old.month, "gone", days=200)

    result = prune(tmp_path)

    assert result.months_removed == 1
    assert not (tmp_path / f"raw/{old.year:04d}/{old.month:02d}").exists()


async def test_a_custom_window_is_honoured(tmp_path: Path) -> None:
    store = LocalRawStore(tmp_path)
    now = datetime.now(UTC)
    page = await _page(store, now.year, now.month, "week", days=8)

    assert prune(tmp_path, older_than_days=7).files == 1
    assert not page.exists()


def test_a_store_that_was_never_written_to_is_not_an_error(tmp_path: Path) -> None:
    """A worker deployed with the volume but before its first crawl."""
    assert prune(tmp_path / "never") == Pruned(0, 0, 0)


def test_a_stray_directory_does_not_stop_the_sweep(tmp_path: Path) -> None:
    """Somebody's mistake under `raw/`. The sweep's job is to remove old pages, not to refuse
    to run because it found something it does not recognise."""
    (tmp_path / "raw" / "notayear" / "notamonth").mkdir(parents=True)
    assert prune(tmp_path).files == 0


def test_a_writable_directory_reports_nothing_wrong(tmp_path: Path) -> None:
    assert writable(tmp_path / "fresh") is None
    assert (tmp_path / "fresh").is_dir(), "and it is created on the way"


def test_a_directory_that_cannot_be_written_to_says_why(tmp_path: Path) -> None:
    """The bug this function exists for, and the reason it writes rather than asking.

    A fresh Docker named volume is created root-owned while the worker runs as uid 1001. The
    directory existed, `mkdir(exist_ok=True)` succeeded, the worker logged "raw store ready", and
    every write then failed into the log line the recorder uses for a full disk. The store was
    deployed and kept nothing for as long as nobody checked.

    `os.chmod` is a no-op for the owner on Windows, so this stands a file where the directory
    should be: `mkdir` on a path that is a file fails the same way a read-only mount does, which
    is the property under test -- that the probe reports a reason instead of claiming success.
    """
    blocked = tmp_path / "blocked"
    blocked.write_bytes(b"not a directory")

    reason = writable(blocked)

    assert reason is not None
    assert "Error" in reason, reason
