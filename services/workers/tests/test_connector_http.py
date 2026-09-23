"""The rate-limited, restriction-aware HTTP client every connector goes through (docs/08).

Tests never hit a live third-party API: responses come from respx and recorded fixtures.
Metering lives in test_connector_metering.py.
"""

import asyncio
import gzip
import json
import time
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from app.connectors import http_client as http_client_module
from app.connectors.http_client import backoff_delay, retry_after_seconds
from app.connectors.types import RateLimit
from app.jobs.errors import (
    AccessRestrictedError,
    InvalidInputError,
    ParseFailedError,
    RateLimitedError,
    TransientError,
)
from tests.connector_support import FREE, URL, make_client
from tests.fixtures import load_bytes, load_json


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps retry tests fast; the delay itself is covered by test_backoff_grows_and_is_capped."""
    monkeypatch.setattr(http_client_module, "backoff_delay", lambda attempt: 0.0)


@respx.mock
async def test_a_successful_call_returns_the_body_and_sends_an_honest_user_agent() -> None:
    route = respx.get(URL).mock(
        return_value=httpx.Response(200, json=load_json("example_source", "search_success"))
    )
    async with make_client() as client:
        response = await client.get(URL, cost=FREE)

    assert response.status_code == 200
    assert len(response.json()["results"]) == 2
    sent = route.calls.last.request
    assert "LeadForgeBot" in sent.headers["user-agent"]


@respx.mock
async def test_an_empty_result_set_is_a_normal_response() -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(200, json=load_json("example_source", "search_empty"))
    )
    async with make_client() as client:
        response = await client.get(URL, cost=FREE)
    assert response.json()["results"] == []


def wire_body(data: bytes) -> AsyncIterator[bytes]:
    """Content exactly as it arrives on the wire; httpx would decode a plain `content=` eagerly."""

    async def stream() -> AsyncIterator[bytes]:
        yield data

    return stream()


@respx.mock
async def test_a_gzipped_response_is_decoded_exactly_once() -> None:
    payload = load_json("example_source", "search_success")
    raw = json.dumps(payload).encode("utf-8")
    respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-encoding": "gzip", "content-type": "application/json"},
            content=wire_body(gzip.compress(raw)),
        )
    )
    async with make_client() as client:
        response = await client.get(URL, cost=FREE)

    # Re-running the gzip decoder over already-decoded bytes used to fail every real API call.
    assert response.json() == payload
    assert "content-encoding" not in response.headers
    # Re-derived for the decoded body; the compressed wire length would be a lie.
    assert response.headers["content-length"] == str(len(raw))


@respx.mock
async def test_a_transient_failure_is_retried_until_it_succeeds() -> None:
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(503, json=load_json("example_source", "error")),
            httpx.ConnectError("connection reset"),
            httpx.Response(200, json=load_json("example_source", "search_success")),
        ]
    )
    async with make_client() as client:
        response = await client.get(URL, cost=FREE)

    assert response.status_code == 200
    assert route.call_count == 3


@respx.mock
async def test_a_transient_failure_gives_up_after_max_attempts() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(502))
    async with make_client(max_attempts=3) as client:
        with pytest.raises(TransientError) as err:
            await client.get(URL, cost=FREE)

    assert route.call_count == 3
    assert "502" in str(err.value)


@respx.mock
async def test_a_429_surfaces_retry_after_and_is_not_retried_here() -> None:
    route = respx.get(URL).mock(
        return_value=httpx.Response(
            429,
            headers={"retry-after": "12"},
            json=load_json("example_source", "rate_limited"),
        )
    )
    async with make_client() as client:
        with pytest.raises(RateLimitedError) as err:
            await client.get(URL, cost=FREE)

    # The job runner owns the wait, so the worker can release the message meanwhile.
    assert route.call_count == 1
    assert err.value.retry_after_s == 12.0


def test_retry_after_accepts_both_rfc_9110_forms() -> None:
    def parse(value: str) -> float | None:
        return retry_after_seconds(httpx.Headers({"retry-after": value}))

    assert parse("30") == 30.0
    assert parse("99999") == http_client_module.MAX_RETRY_AFTER_S
    assert parse("-5") == 0.0
    # An HTTP date must be honoured too, or we hammer a source that asked us to wait.
    assert parse("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0  # long past: wait nothing
    assert parse("Fri, 01 Jan 2100 00:00:00 GMT") == http_client_module.MAX_RETRY_AFTER_S
    assert parse("soon please") is None
    assert retry_after_seconds(httpx.Headers({})) is None


@respx.mock
async def test_a_blocked_response_stops_immediately_and_is_never_retried() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(403, text="Forbidden"))
    async with make_client() as client:
        with pytest.raises(AccessRestrictedError) as err:
            await client.get(URL, cost=FREE)

    assert route.call_count == 1
    assert "http_403" in str(err.value)


@respx.mock
async def test_a_bot_challenge_page_behind_a_200_is_restricted_not_parsed() -> None:
    route = respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=load_bytes("example_source", "restricted.html"),
        )
    )
    async with make_client() as client:
        with pytest.raises(AccessRestrictedError) as err:
            await client.get(URL, cost=FREE)

    assert route.call_count == 1
    assert "bot_challenge" in str(err.value)


@respx.mock
async def test_a_redirect_loop_counts_as_a_block_not_a_transient_failure() -> None:
    # A consent or login wall often shows up as an endless bounce; retrying it is pointless.
    respx.get(URL).mock(return_value=httpx.Response(302, headers={"location": URL}))
    async with make_client(max_redirects=2) as client:
        with pytest.raises(AccessRestrictedError) as err:
            await client.get(URL, cost=FREE)
    assert "redirect_loop" in str(err.value)


@respx.mock
async def test_an_undecodable_body_fails_the_parse_instead_of_looping() -> None:
    route = respx.get(URL).mock(
        return_value=httpx.Response(
            200, headers={"content-encoding": "gzip"}, content=wire_body(b"this is not gzip")
        )
    )
    async with make_client() as client:
        with pytest.raises(ParseFailedError):
            await client.get(URL, cost=FREE)
    assert route.call_count == 1


@respx.mock
async def test_an_oversized_response_is_rejected_without_being_buffered() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"x" * 5000))
    async with make_client(max_bytes=1024) as client:
        with pytest.raises(ParseFailedError) as err:
            await client.get(URL, cost=FREE)
    assert "larger than 1024 bytes" in str(err.value)


@respx.mock
async def test_a_source_level_error_status_is_handed_to_the_connector() -> None:
    # Only the connector knows whether 404 means "no such place" or something worse.
    respx.get(URL).mock(return_value=httpx.Response(404, json={"error": {"code": 404}}))
    async with make_client() as client:
        response = await client.get(URL, cost=FREE)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == 404


async def test_a_non_http_url_is_rejected_before_any_request() -> None:
    async with make_client() as client:
        for url in ("file:///etc/passwd", "ftp://example.test/data"):
            with pytest.raises(InvalidInputError):
                await client.get(url, cost=FREE)


@respx.mock
async def test_concurrency_is_capped_per_connector() -> None:
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return httpx.Response(200, json={"results": []})

    respx.get(URL).mock(side_effect=handler)
    limit = RateLimit(requests=100, per_seconds=1.0, concurrency=2)
    async with make_client(rate_limit=limit) as client:
        await asyncio.gather(*(client.get(URL, cost=FREE, params={"i": i}) for i in range(6)))

    # Exactly two: fewer would mean the calls are serialised, more would break the source's limit.
    assert peak == 2


@respx.mock
async def test_the_rate_limit_spaces_calls_out() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={"results": []}))
    limit = RateLimit(requests=1, per_seconds=0.2, concurrency=4)
    started = time.monotonic()
    async with make_client(rate_limit=limit) as client:
        await asyncio.gather(*(client.get(URL, cost=FREE, params={"i": i}) for i in range(3)))
    # Three calls at one per 200ms cannot finish before the second window has elapsed.
    assert time.monotonic() - started >= 0.4


def test_backoff_grows_and_is_capped() -> None:
    # Imported directly, so the no-backoff fixture's module patch does not hide the real one.
    delays = [backoff_delay(attempt) for attempt in range(1, 8)]
    assert min(delays) >= 0.25
    assert min(delays[:2]) < max(delays[2:])
    assert max(delays) <= 4.0 * 1.5


@respx.mock
async def test_the_body_is_read_once_and_stays_available() -> None:
    payload = load_json("example_source", "search_success")
    respx.get(URL).mock(
        return_value=httpx.Response(200, content=json.dumps(payload).encode("utf-8"))
    )
    async with make_client() as client:
        response = await client.get(URL, cost=FREE)
    assert response.json() == payload
    assert response.content == json.dumps(payload).encode("utf-8")
