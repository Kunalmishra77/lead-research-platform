"""Fetching one page of a lead's own website (docs/06 section 4, task 3.3).

The send itself stays in `ConnectorHttpClient` (ADR-0013): HTTP/2, the streamed 5 MB body cap, the
honest User-Agent, transient-only retries, `Retry-After` and the restriction detector are all there
already and none of it should exist twice. What this adds is the policy a crawler needs and an API
connector does not.

**Redirects are followed here, one hop at a time, and that is the point.** The SSRF guard and
robots.txt both answer questions about a URL, so checking the URL we were given and then letting
httpx follow wherever it leads checks the wrong thing: `https://example.com/go` redirecting to
`http://127.0.0.1/` passes every check and then fetches from inside our own network. It is the
oldest SSRF bypass there is. So the underlying client is built with redirects off and each hop is
checked before it is taken.

**robots.txt is consulted per hop too**, because a redirect into a disallowed path is still a
fetch of a disallowed path.

**Politeness is per host, not per connector.** A connector talks to one API; a crawl talks to
thousands of sites at once, so one rate limiter for the lot would be either useless or crippling.
Each host gets a lock and a last-fetched time, and waits the longer of our floor and whatever
robots.txt asked for.
"""

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
import structlog

from app.connectors.factory import USER_AGENT
from app.connectors.http_client import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TIMEOUT_S,
    CallCost,
    ConnectorHttpClient,
)
from app.connectors.types import RateLimit
from app.crawl.robots import Decision, RobotsPolicy
from app.crawl.safety import MAX_REDIRECTS, SafeResolver
from app.jobs.errors import AccessRestrictedError

log = structlog.get_logger(__name__)

#: docs/06 section 4.3. The floor between two requests to one host, when robots.txt asks for less.
MIN_HOST_DELAY_S = 1.0

#: And the ceiling, when it asks for more. A site may legitimately want a long delay, but a crawl
#: that honours `Crawl-delay: 3600` sits on one task for an hour and times out anyway. Past this we
#: stop rather than pretend: the target is recorded as deferred, not as having no pages.
MAX_HONOURED_DELAY_S = 30.0

#: How many hosts' timings to remember. A crawl spanning more hosts than this loses the oldest
#: entries, which costs an extra polite wait, never a rude one.
HOST_MEMORY = 4096

#: Crawling a site costs us nothing to pay for, so nothing is metered and no spend is reserved.
#: The client's size cap, retries and restriction detection all still apply.
_FREE = CallCost(meter="crawl_page", cost_micros=0)


@dataclass(frozen=True, slots=True)
class Fetched:
    """One page, or the reason there is no page."""

    #: The URL the content actually came from, after redirects.
    url: str
    status: int
    body: bytes
    headers: httpx.Headers
    #: True when the server said 304: we already have this content, unchanged.
    not_modified: bool = False
    #: Carried forward so the next crawl can ask conditionally (task 3.6 stores them).
    etag: str | None = None
    last_modified: str | None = None
    #: How many redirects were followed to get here.
    hops: int = 0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class HostPacer:
    """Keeps requests to one host one-at-a-time and spaced out."""

    def __init__(self, *, min_delay_s: float = MIN_HOST_DELAY_S, memory: int = HOST_MEMORY) -> None:
        self._min_delay = min_delay_s
        self._memory = memory
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._last: dict[str, float] = {}

    def _lock_for(self, host: str) -> asyncio.Lock:
        lock = self._locks.get(host)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[host] = lock
            while len(self._locks) > self._memory:
                # Two hosts are never forgotten: one whose lock is held, because dropping it would
                # let two requests at that host overlap, which is the one thing this class exists
                # to prevent; and the one just added, because the caller is about to use it. With
                # a small memory and a busy host the map grows past its bound for a moment, which
                # is the right way round to be wrong.
                for old, old_lock in list(self._locks.items()):
                    if old != host and not old_lock.locked():
                        self._locks.pop(old, None)
                        self._last.pop(old, None)
                        break
                else:
                    break
        self._locks.move_to_end(host)
        return lock

    async def wait(self, host: str, crawl_delay_s: float | None) -> None:
        """Waits until it is polite to ask this host again. Call inside `hold`."""
        delay = max(self._min_delay, crawl_delay_s or 0.0)
        since = time.monotonic() - self._last.get(host, 0.0)
        if since < delay:
            await asyncio.sleep(delay - since)

    def done(self, host: str) -> None:
        self._last[host] = time.monotonic()

    def hold(self, host: str) -> asyncio.Lock:
        """The per-host lock, so a caller can `async with` it around wait + fetch + done."""
        return self._lock_for(host)


class CrawlFetcher:
    """Fetches pages of sites we do not own, under robots.txt and the SSRF guard."""

    def __init__(
        self,
        http: ConnectorHttpClient,
        robots: RobotsPolicy,
        *,
        resolver: SafeResolver | None = None,
        pacer: HostPacer | None = None,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        self._http = http
        self._robots = robots
        self._resolver = resolver or SafeResolver()
        self._pacer = pacer or HostPacer()
        self._max_redirects = max_redirects

    async def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> Fetched:
        """One page, following redirects by hand so every hop is checked.

        Raises `AccessRestrictedError` when robots.txt disallows the path or the site turns us
        away, and `InvalidInputError` when the URL points somewhere we must not reach. Both are
        terminal: nothing retries them and nothing works around them (CLAUDE.md).
        """
        current = url
        for hop in range(self._max_redirects + 1):
            decision = await self._allowed(current)
            response = await self._send(current, decision.crawl_delay, etag, last_modified)

            if response.status_code == 304:
                # The server is telling us our copy is current. No body, and none expected.
                return Fetched(
                    url=current,
                    status=304,
                    body=b"",
                    headers=response.headers,
                    not_modified=True,
                    etag=etag,
                    last_modified=last_modified,
                    hops=hop,
                )

            location = _redirect_target(response, current)
            if location is None:
                return Fetched(
                    url=current,
                    status=response.status_code,
                    body=response.content,
                    headers=response.headers,
                    etag=response.headers.get("etag"),
                    last_modified=response.headers.get("last-modified"),
                    hops=hop,
                )

            # A conditional request's validators belong to the URL we asked, not to wherever it
            # points. Carrying them across a hop asks the next server about a copy it never sent.
            etag = last_modified = None
            current = location

        raise AccessRestrictedError(
            f"redirect_limit: more than {self._max_redirects} hops from {url}"
        )

    async def _allowed(self, url: str) -> Decision:
        """robots and SSRF for one hop, in that order. Returns the robots decision."""
        # robots first: it costs one cached read, and being told not to fetch a path means we never
        # resolve its host at all.
        decision = await self._robots.decide(url)
        if not decision.allowed:
            log.info("crawl refused by robots", url=url, reason=decision.reason)
            raise AccessRestrictedError(f"{decision.reason}: {url}")
        self._resolver.check(url)
        return decision

    async def _send(
        self,
        url: str,
        crawl_delay_s: float | None,
        etag: str | None,
        last_modified: str | None,
    ) -> httpx.Response:
        if crawl_delay_s is not None and crawl_delay_s > MAX_HONOURED_DELAY_S:
            # Honouring it would hold a task open for minutes and time out anyway; saying so is
            # more useful than a crawl that silently finds nothing.
            raise AccessRestrictedError(
                f"crawl_delay_too_long: {crawl_delay_s:g}s requested by {urlsplit(url).netloc}"
            )
        headers: dict[str, str] = {}
        if etag:
            headers["if-none-match"] = etag
        if last_modified:
            headers["if-modified-since"] = last_modified

        host = urlsplit(url).netloc
        async with self._pacer.hold(host):
            await self._pacer.wait(host, crawl_delay_s)
            try:
                return await self._http.get(url, cost=_FREE, headers=headers)
            finally:
                self._pacer.done(host)


def _redirect_target(response: httpx.Response, base: str) -> str | None:
    """The absolute URL a 3xx points at, or None when the response is not a redirect."""
    if response.status_code not in (301, 302, 303, 307, 308):
        return None
    location = response.headers.get("location")
    if not location:
        return None
    # urljoin is untyped in the stubs for this call shape; the result is a str.
    return str(urljoin(base, location.strip()))


def ensure_fetchable(url: str, resolver: SafeResolver | None = None) -> None:
    """The SSRF check on its own, for callers deciding whether a URL is worth queueing at all."""
    (resolver or SafeResolver()).check(url)


def crawl_http_client(
    *,
    user_agent: str = USER_AGENT,
    requests_per_second: float = 4.0,
    concurrency: int = 8,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> ConnectorHttpClient:
    """A client configured for crawling, chiefly so `follow_redirects` cannot be left on.

    `CrawlFetcher` checks robots.txt and the SSRF guard on every hop, which only works if httpx is
    not quietly following them first. Building the client by hand and forgetting that flag is the
    one mistake here that fails silently and safely-looking, so it is not left to memory.

    The limiter is a global ceiling on how fast this worker crawls anything, a courtesy to the
    network we sit on. Per-host politeness is `HostPacer`'s job and is much stricter.
    """
    return ConnectorHttpClient(
        source_key="crawl",
        rate_limit=RateLimit(
            requests=int(requests_per_second), per_seconds=1.0, concurrency=concurrency
        ),
        user_agent=user_agent,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
        client=httpx.AsyncClient(
            http2=True,
            timeout=timeout_s,
            follow_redirects=False,
            headers={"user-agent": user_agent},
        ),
    )


__all__ = [
    "MAX_HONOURED_DELAY_S",
    "MIN_HOST_DELAY_S",
    "CrawlFetcher",
    "Fetched",
    "HostPacer",
    "crawl_http_client",
    "ensure_fetchable",
]
