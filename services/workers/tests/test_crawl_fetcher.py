"""Fetching a page of someone else's site (docs/06 section 4, task 3.3).

The send itself is `ConnectorHttpClient`, already covered by test_connector_http.py. What is tested
here is the policy around it: robots and the SSRF guard on every hop, conditional requests, and
per-host politeness.
"""

import asyncio
from itertools import pairwise

import httpx
import pytest
import respx

from app.crawl.fetcher import (
    MAX_HONOURED_DELAY_S,
    CrawlFetcher,
    HostPacer,
    crawl_http_client,
)
from app.crawl.robots import RobotsPolicy
from app.jobs.errors import AccessRestrictedError, InvalidInputError
from tests.test_crawl_robots import ALLOW_MOST, FakeFetcher, FakeRedis

PAGE = b"<html><body><h1>Contact</h1><a href='mailto:hi@example.test'>hi</a></body></html>"


class AllowAll:
    """A resolver that answers yes, so a test can exercise everything except DNS."""

    def check(self, url: str) -> None:
        return None


class RefuseAll:
    def check(self, url: str) -> None:
        raise InvalidInputError("unsafe url: private_address: 127.0.0.1")


def make_fetcher(
    *,
    robots_body: bytes = ALLOW_MOST,
    robots_status: int = 200,
    resolver: object | None = None,
    pacer: HostPacer | None = None,
) -> tuple[CrawlFetcher, object]:
    http = crawl_http_client()
    robots = RobotsPolicy(
        FakeRedis(), FakeFetcher(robots_status, robots_body), user_agent="LeadForgeBot/1.0"
    )
    fetcher = CrawlFetcher(
        http,
        robots,
        resolver=resolver or AllowAll(),  # type: ignore[arg-type]
        pacer=pacer or HostPacer(min_delay_s=0.0),
    )
    return fetcher, http


@pytest.mark.asyncio
@respx.mock
async def test_an_allowed_page_comes_back_with_its_validators() -> None:
    respx.get("https://example.test/contact").mock(
        return_value=httpx.Response(
            200,
            content=PAGE,
            headers={"etag": '"abc"', "last-modified": "Wed, 01 Oct 2026 10:00:00 GMT"},
        )
    )
    fetcher, http = make_fetcher()
    try:
        page = await fetcher.fetch("https://example.test/contact")
    finally:
        await http.aclose()
    assert page.ok
    assert page.body == PAGE
    assert page.etag == '"abc"'
    assert page.last_modified == "Wed, 01 Oct 2026 10:00:00 GMT"
    assert page.hops == 0


@pytest.mark.asyncio
@respx.mock
async def test_a_disallowed_path_is_never_requested() -> None:
    route = respx.get("https://example.test/admin/secrets").mock(
        return_value=httpx.Response(200, content=PAGE)
    )
    fetcher, http = make_fetcher()
    try:
        with pytest.raises(AccessRestrictedError, match="robots_disallow"):
            await fetcher.fetch("https://example.test/admin/secrets")
    finally:
        await http.aclose()
    # The point is not the exception; it is that nothing was fetched.
    assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_conditional_headers_are_sent_and_a_304_carries_them_forward() -> None:
    route = respx.get("https://example.test/about").mock(return_value=httpx.Response(304))
    fetcher, http = make_fetcher()
    try:
        page = await fetcher.fetch(
            "https://example.test/about", etag='"v1"', last_modified="Tue, 30 Sep 2026 09:00:00 GMT"
        )
    finally:
        await http.aclose()
    sent = route.calls[0].request
    assert sent.headers["if-none-match"] == '"v1"'
    assert sent.headers["if-modified-since"] == "Tue, 30 Sep 2026 09:00:00 GMT"
    assert page.not_modified
    assert page.status == 304
    assert page.body == b""
    # The validators are the ones we still hold, so the caller can store them unchanged.
    assert page.etag == '"v1"'


@pytest.mark.asyncio
@respx.mock
async def test_a_redirect_is_followed_and_counted() -> None:
    respx.get("https://example.test/old").mock(
        return_value=httpx.Response(301, headers={"location": "/new"})
    )
    respx.get("https://example.test/new").mock(return_value=httpx.Response(200, content=PAGE))
    fetcher, http = make_fetcher()
    try:
        page = await fetcher.fetch("https://example.test/old")
    finally:
        await http.aclose()
    assert page.url == "https://example.test/new"
    assert page.hops == 1
    assert page.body == PAGE


@pytest.mark.asyncio
@respx.mock
async def test_a_redirect_into_a_disallowed_path_is_refused() -> None:
    respx.get("https://example.test/go").mock(
        return_value=httpx.Response(302, headers={"location": "/admin/panel"})
    )
    blocked = respx.get("https://example.test/admin/panel").mock(
        return_value=httpx.Response(200, content=PAGE)
    )
    fetcher, http = make_fetcher()
    try:
        with pytest.raises(AccessRestrictedError, match="robots_disallow"):
            await fetcher.fetch("https://example.test/go")
    finally:
        await http.aclose()
    assert not blocked.called


@pytest.mark.asyncio
@respx.mock
async def test_a_redirect_to_a_private_address_is_refused_at_that_hop() -> None:
    # The oldest SSRF bypass: the first URL is fine and the redirect is not. Checking only the URL
    # we were handed and letting the client follow it would fetch from inside our own network.
    class OnlyFirstHostIsPublic:
        def check(self, url: str) -> None:
            if "example.test" not in url:
                raise InvalidInputError("unsafe url: loopback_address: 127.0.0.1")

    respx.get("https://example.test/go").mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
    )
    inside = respx.get("http://127.0.0.1/admin").mock(
        return_value=httpx.Response(200, content=b"secrets")
    )
    fetcher, http = make_fetcher(resolver=OnlyFirstHostIsPublic())
    try:
        with pytest.raises(InvalidInputError, match="loopback_address"):
            await fetcher.fetch("https://example.test/go")
    finally:
        await http.aclose()
    assert not inside.called


@pytest.mark.asyncio
@respx.mock
async def test_validators_are_not_carried_across_a_redirect() -> None:
    # They describe the copy the first server sent; the second one never sent it.
    respx.get("https://example.test/old").mock(
        return_value=httpx.Response(301, headers={"location": "/new"})
    )
    route = respx.get("https://example.test/new").mock(
        return_value=httpx.Response(200, content=PAGE)
    )
    fetcher, http = make_fetcher()
    try:
        await fetcher.fetch("https://example.test/old", etag='"v1"')
    finally:
        await http.aclose()
    assert "if-none-match" not in route.calls[0].request.headers


@pytest.mark.asyncio
@respx.mock
async def test_a_redirect_chain_that_never_lands_is_refused() -> None:
    respx.get("https://example.test/loop").mock(
        return_value=httpx.Response(302, headers={"location": "/loop"})
    )
    fetcher, http = make_fetcher()
    try:
        with pytest.raises(AccessRestrictedError, match="redirect_limit"):
            await fetcher.fetch("https://example.test/loop")
    finally:
        await http.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_the_ssrf_guard_runs_before_any_request() -> None:
    route = respx.get("https://example.test/x").mock(return_value=httpx.Response(200))
    fetcher, http = make_fetcher(resolver=RefuseAll())
    try:
        with pytest.raises(InvalidInputError, match="private_address"):
            await fetcher.fetch("https://example.test/x")
    finally:
        await http.aclose()
    assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_a_site_that_turns_us_away_is_access_restricted_not_retried() -> None:
    route = respx.get("https://example.test/members").mock(return_value=httpx.Response(403))
    fetcher, http = make_fetcher()
    try:
        with pytest.raises(AccessRestrictedError):
            await fetcher.fetch("https://example.test/members")
    finally:
        await http.aclose()
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_an_absurd_crawl_delay_stops_rather_than_waits() -> None:
    respx.get("https://slow.test/").mock(return_value=httpx.Response(200, content=PAGE))
    body = f"User-agent: *\nCrawl-delay: {MAX_HONOURED_DELAY_S + 1:g}\n".encode()
    fetcher, http = make_fetcher(robots_body=body)
    try:
        with pytest.raises(AccessRestrictedError, match="crawl_delay_too_long"):
            await fetcher.fetch("https://slow.test/")
    finally:
        await http.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_the_client_this_builds_does_not_follow_redirects_itself() -> None:
    # If it did, every per-hop check above would be checking the wrong URL and still passing.
    http = crawl_http_client()
    try:
        assert http._client.follow_redirects is False
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_the_pacer_spaces_requests_to_one_host() -> None:
    pacer = HostPacer(min_delay_s=0.05)
    started: list[float] = []

    async def visit() -> None:
        async with pacer.hold("example.test"):
            await pacer.wait("example.test", None)
            started.append(asyncio.get_running_loop().time())
            pacer.done("example.test")

    await asyncio.gather(visit(), visit(), visit())
    gaps = [b - a for a, b in pairwise(started)]
    assert all(gap >= 0.04 for gap in gaps), gaps


@pytest.mark.asyncio
async def test_the_pacer_does_not_make_one_host_wait_for_another() -> None:
    pacer = HostPacer(min_delay_s=0.2)

    async def visit(host: str) -> None:
        async with pacer.hold(host):
            await pacer.wait(host, None)
            pacer.done(host)

    loop = asyncio.get_running_loop()
    before = loop.time()
    await asyncio.gather(visit("a.test"), visit("b.test"), visit("c.test"))
    # Three different hosts, none of which has been seen before: no waiting at all.
    assert loop.time() - before < 0.2


@pytest.mark.asyncio
async def test_the_pacer_forgets_idle_hosts_rather_than_growing_for_ever() -> None:
    pacer = HostPacer(min_delay_s=0.0, memory=4)
    for i in range(20):
        async with pacer.hold(f"host{i}.test"):
            pacer.done(f"host{i}.test")
    assert len(pacer._locks) <= 4


@pytest.mark.asyncio
async def test_the_pacer_never_forgets_a_host_it_is_holding() -> None:
    # Dropping a held lock would let two requests to one host overlap, which is the one thing the
    # pacer exists to prevent.
    pacer = HostPacer(min_delay_s=0.0, memory=1)
    held = pacer.hold("busy.test")
    async with held:
        for i in range(5):
            async with pacer.hold(f"other{i}.test"):
                pass
        assert pacer._locks.get("busy.test") is held
