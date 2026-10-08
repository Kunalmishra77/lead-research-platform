"""robots.txt (docs/06 section 4.2, task 3.2). Phase 3 acceptance: a disallow is never fetched."""

from typing import Any

import pytest

from app.crawl.robots import (
    CACHE_TTL_SECONDS,
    UNAVAILABLE_TTL_SECONDS,
    RobotsPolicy,
    origin_of,
    path_of,
)

UA = "LeadForgeBot/1.0 (+https://leadforge.example/bot)"

ALLOW_MOST = b"""
User-agent: *
Disallow: /admin/
Disallow: /cart
Crawl-delay: 2

User-agent: GreedyBot
Disallow: /
"""


class FakeRedis:
    """Enough of redis.asyncio for this module: get, set with ex, and a failure switch."""

    def __init__(self, *, broken: bool = False) -> None:
        self.store: dict[str, tuple[str, int | None]] = {}
        self.broken = broken
        self.gets = 0

    async def get(self, key: str) -> str | None:
        self.gets += 1
        if self.broken:
            raise RuntimeError("redis is down")
        entry = self.store.get(key)
        return entry[0] if entry else None

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self.broken:
            raise RuntimeError("redis is down")
        self.store[key] = (value, ex)


class FakeFetcher:
    def __init__(self, status: int, body: bytes = b"", *, raises: bool = False) -> None:
        self.status = status
        self.body = body
        self.raises = raises
        self.calls: list[str] = []

    async def __call__(self, url: str) -> tuple[int, bytes]:
        self.calls.append(url)
        if self.raises:
            raise RuntimeError("connection reset")
        return self.status, self.body


def policy(redis: Any, fetcher: Any) -> RobotsPolicy:
    return RobotsPolicy(redis, fetcher, user_agent=UA)


def test_origin_and_path_split_the_way_protego_expects() -> None:
    assert origin_of("https://Example.COM:443/a/b?x=1") == "https://example.com:443"
    assert path_of("https://example.com/a/b?x=1") == "/a/b?x=1"
    # A query can change which rule matches, so it is not dropped.
    assert path_of("https://example.com/search?q=1") == "/search?q=1"
    assert path_of("https://example.com") == "/"


@pytest.mark.asyncio
async def test_an_allowed_path_is_allowed_and_carries_the_crawl_delay() -> None:
    decision = await policy(FakeRedis(), FakeFetcher(200, ALLOW_MOST)).decide(
        "https://example.com/contact"
    )
    assert decision.allowed
    assert decision.reason == "robots_allow"
    assert decision.crawl_delay == 2.0


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://example.com/admin/users", "https://example.com/cart"])
async def test_a_disallowed_path_is_refused(url: str) -> None:
    decision = await policy(FakeRedis(), FakeFetcher(200, ALLOW_MOST)).decide(url)
    assert not decision.allowed
    assert decision.reason == "robots_disallow"


@pytest.mark.asyncio
async def test_a_rule_for_another_bot_does_not_bind_us() -> None:
    # GreedyBot is disallowed everything; we are not GreedyBot and must not inherit its block.
    decision = await policy(FakeRedis(), FakeFetcher(200, ALLOW_MOST)).decide(
        "https://example.com/about"
    )
    assert decision.allowed


@pytest.mark.asyncio
async def test_a_rule_naming_us_binds_us() -> None:
    body = b"User-agent: LeadForgeBot\nDisallow: /\n"
    decision = await policy(FakeRedis(), FakeFetcher(200, body)).decide("https://example.com/")
    assert not decision.allowed
    assert decision.reason == "robots_disallow"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410, 401, 403])
async def test_no_robots_file_means_no_rules(status: int) -> None:
    decision = await policy(FakeRedis(), FakeFetcher(status)).decide("https://example.com/x")
    assert decision.allowed
    assert decision.reason == "robots_absent"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503])
async def test_a_server_that_cannot_tell_us_the_rules_is_not_consent(status: int) -> None:
    # RFC 9309 treats unavailable as a complete disallow. Reading 5xx as permission would mean
    # crawling a site during its outage precisely because it was broken.
    decision = await policy(FakeRedis(), FakeFetcher(status)).decide("https://example.com/x")
    assert not decision.allowed
    assert decision.reason == "robots_unavailable"


@pytest.mark.asyncio
async def test_a_failed_fetch_is_treated_as_unavailable_not_as_permission() -> None:
    decision = await policy(FakeRedis(), FakeFetcher(0, raises=True)).decide(
        "https://example.com/x"
    )
    assert not decision.allowed
    assert decision.reason == "robots_unavailable"


@pytest.mark.asyncio
async def test_rules_are_cached_for_a_day_and_an_outage_only_for_minutes() -> None:
    redis = FakeRedis()
    await policy(redis, FakeFetcher(200, ALLOW_MOST)).decide("https://example.com/a")
    assert redis.store["robots:https://example.com"][1] == CACHE_TTL_SECONDS

    outage = FakeRedis()
    await policy(outage, FakeFetcher(503)).decide("https://down.example.com/a")
    # A site having a bad minute has not set a policy, so its block is not remembered for a day.
    assert outage.store["robots:https://down.example.com"][1] == UNAVAILABLE_TTL_SECONDS


@pytest.mark.asyncio
async def test_one_fetch_serves_every_page_of_a_site() -> None:
    redis = FakeRedis()
    fetcher = FakeFetcher(200, ALLOW_MOST)
    subject = policy(redis, fetcher)
    for path in ("/", "/about", "/contact", "/admin/x"):
        await subject.decide(f"https://example.com{path}")
    assert fetcher.calls == ["https://example.com/robots.txt"]


@pytest.mark.asyncio
async def test_two_origins_of_one_host_do_not_share_rules() -> None:
    redis = FakeRedis()
    fetcher = FakeFetcher(200, ALLOW_MOST)
    subject = policy(redis, fetcher)
    await subject.decide("https://example.com/a")
    await subject.decide("http://example.com/a")
    assert len(fetcher.calls) == 2


@pytest.mark.asyncio
async def test_an_unreadable_cache_is_a_miss_and_never_a_licence() -> None:
    # Redis being down must not turn a disallow into an allow: it falls back to fetching.
    redis = FakeRedis(broken=True)
    fetcher = FakeFetcher(200, ALLOW_MOST)
    decision = await policy(redis, fetcher).decide("https://example.com/admin/x")
    assert not decision.allowed
    assert fetcher.calls == ["https://example.com/robots.txt"]


@pytest.mark.asyncio
async def test_a_huge_robots_file_is_truncated_rather_than_read_whole() -> None:
    # An unbounded read of a file an untrusted server controls is a denial of service on us.
    body = b"User-agent: *\nDisallow: /admin/\n" + b"# padding\n" * 200_000
    decision = await policy(FakeRedis(), FakeFetcher(200, body)).decide(
        "https://example.com/admin/x"
    )
    assert not decision.allowed
