"""Fetching an extract, against recorded responses. Never the live site (docs/08).

The behaviour worth pinning down is not the happy path but the three ways a 1.6 GB download goes
wrong quietly: a truncated transfer that parses into a partial index, a re-download of a file we
already have, and a partial file left where the real one should be.
"""

import hashlib
from pathlib import Path

import httpx
import pytest
import respx

from app.osm.download import REGIONS, fetch_extract, region_url

_URL = "https://download.geofabrik.de/asia/india/northern-zone-latest.osm.pbf"
_BODY = b"not really a pbf, but the bytes are what is checksummed"
_MD5 = hashlib.md5(_BODY, usedforsecurity=False).hexdigest()
_LAST_MODIFIED = "Fri, 09 Oct 2026 22:43:32 GMT"


def test_region_url_shapes_the_known_regions() -> None:
    assert region_url("india") == "https://download.geofabrik.de/asia/india-latest.osm.pbf"
    assert region_url("india-northern") == _URL


def test_an_unknown_region_names_the_ones_that_exist() -> None:
    """An operator typo should not become a 404 three minutes into a download."""
    with pytest.raises(ValueError, match="unknown region") as caught:
        region_url("narnia")
    assert "india-northern" in str(caught.value)


def test_every_region_has_a_well_formed_url() -> None:
    for region in REGIONS:
        assert region_url(region).endswith("-latest.osm.pbf")


def _route(body: bytes = _BODY, md5: str | None = None, status: int = 200) -> respx.Route:
    """Registers the three routes an extract fetch touches, and returns the download one.

    Returned rather than looked up later because `respx.get(url)` *registers* a route; calling it
    again to read `.call_count` quietly shadows the configured one with an empty default, which
    is how an earlier version of this test "downloaded" zero bytes on its second pass.
    """
    respx.head(_URL).mock(
        return_value=httpx.Response(
            200, headers={"Last-Modified": _LAST_MODIFIED, "Content-Length": str(len(body))}
        )
    )
    # `side_effect` rather than `return_value`: respx hands back the same Response object every
    # time, and this code reads it as a stream, so a second call to a `return_value` route sees
    # an exhausted body and downloads zero bytes. Building a fresh response per call is what a
    # real server does.
    download = respx.get(_URL).mock(
        side_effect=lambda _request: httpx.Response(status, content=body)
    )
    respx.get(f"{_URL}.md5").mock(
        return_value=httpx.Response(200, text=f"{md5 or _MD5}  northern-zone-latest.osm.pbf")
    )
    return download


@respx.mock
def test_a_download_lands_with_the_data_date_it_was_published_with(tmp_path: Path) -> None:
    _route()
    extract = fetch_extract("india-northern", tmp_path)

    assert extract.downloaded is True
    assert extract.path.read_bytes() == _BODY
    assert extract.size_bytes == len(_BODY)
    assert extract.published_at is not None
    # The date OSM cut the data, which is the freshness every value from it inherits -- not the
    # moment we happened to download it.
    assert extract.published_at.date().isoformat() == "2026-10-09"


@respx.mock
def test_a_second_run_transfers_nothing(tmp_path: Path) -> None:
    """A daily refresh that finds nothing new costs one HEAD, so cron can run it without thought."""
    download = _route()
    fetch_extract("india-northern", tmp_path)
    assert download.call_count == 1

    again = fetch_extract("india-northern", tmp_path)
    assert again.downloaded is False
    assert download.call_count == 1


@respx.mock
def test_force_re_downloads_a_file_that_is_already_current(tmp_path: Path) -> None:
    download = _route()
    fetch_extract("india-northern", tmp_path)

    again = fetch_extract("india-northern", tmp_path, force=True)
    assert again.downloaded is True
    assert download.call_count == 2


@respx.mock
def test_a_truncated_download_is_refused_and_deleted(tmp_path: Path) -> None:
    """The failure this exists for: bytes that parse into a quietly incomplete index.

    The file is removed rather than left in place, so the next run cannot mistake it for a good
    copy and skip the download on size alone.
    """
    _route(md5="0" * 32)
    with pytest.raises(OSError, match="checksum mismatch"):
        fetch_extract("india-northern", tmp_path)
    assert not (tmp_path / "india-northern-latest.osm.pbf").exists()


@respx.mock
def test_a_missing_checksum_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    """Geofabrik publishes an MD5 today; the download must not stop the day it does not."""
    _route()
    respx.get(f"{_URL}.md5").mock(return_value=httpx.Response(404))
    extract = fetch_extract("india-northern", tmp_path)
    assert extract.downloaded is True
    assert extract.path.read_bytes() == _BODY


@respx.mock
def test_a_failed_transfer_leaves_the_previous_copy_intact(tmp_path: Path) -> None:
    """A half-written file must never take the real path: the next run compares against it."""
    _route()
    fetch_extract("india-northern", tmp_path)
    dest = tmp_path / "india-northern-latest.osm.pbf"
    assert dest.read_bytes() == _BODY

    respx.get(_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_extract("india-northern", tmp_path, force=True)

    assert dest.read_bytes() == _BODY
    assert not dest.with_suffix(dest.suffix + ".part").exists()
