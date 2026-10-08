"""robots.txt: whether we are allowed to fetch a page at all (docs/06 section 4.2, task 3.2).

A disallow is not an obstacle to be worked around. CLAUDE.md puts robots.txt in the same sentence
as CAPTCHAs and logins: the crawler stops and the target is marked `access_restricted`.

Three decisions worth stating, because each one is a place where "obviously" points the wrong way.

**A missing robots.txt allows everything; an unavailable one allows nothing.** RFC 9309 draws that
line at the status code: 4xx means the file is not there, which is the web's way of saying there
are no rules, while 5xx means the server could not tell us what the rules are. Treating the second
case as permission would mean crawling a site during its outage precisely because it was broken.

**The cached thing is the text, not the parse.** A parsed object cannot be shared between worker
processes or survive a restart, and the whole point of a 24-hour cache is that one fetch serves
every page of every crawl of that site. Parsing is re-done from the text and memoised in-process.

**Redis being down does not grant permission.** A cache miss means fetch; a cache *error* means we
do not know, and not knowing is not a yes.
"""

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import structlog
from protego import Protego
from redis.asyncio import Redis

from app.connectors.factory import USER_AGENT

log = structlog.get_logger(__name__)

#: docs/06 section 4.2. One fetch serves every page of every crawl of a site for a day.
CACHE_TTL_SECONDS = 24 * 60 * 60

#: Nothing legitimate needs more than this, and an unbounded read is a denial-of-service on us.
MAX_ROBOTS_BYTES = 512 * 1024

#: Sentinel stored when the server said "no rules here" (4xx). Distinct from an empty file, which
#: also means no rules, so they behave the same -- but the log line is different and that matters
#: when a crawl finds nothing and someone asks why.
_ABSENT = "\x00absent"

#: Sentinel stored when the server could not say (5xx). Cached briefly, not for a day: the site is
#: having a bad minute, not setting a policy.
_UNAVAILABLE = "\x00unavailable"
UNAVAILABLE_TTL_SECONDS = 5 * 60


@dataclass(frozen=True, slots=True)
class Decision:
    """Whether a URL may be fetched, and why not when it may not."""

    allowed: bool
    reason: str
    #: Seconds a polite crawler waits between requests to this host, when robots.txt says.
    crawl_delay: float | None = None


class RobotsFetcher(Protocol):
    """Fetches one robots.txt. Returns (status, body); the body is ignored unless status is 2xx."""

    async def __call__(self, url: str) -> tuple[int, bytes]: ...


def origin_of(url: str) -> str:
    """The scheme-host-port a robots.txt governs. Two origins never share rules, even one host."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), "", "", ""))


def path_of(url: str) -> str:
    """What protego matches against: path plus query, which can change what a rule covers."""
    parts = urlsplit(url)
    path = parts.path or "/"
    return f"{path}?{parts.query}" if parts.query else path


class RobotsPolicy:
    """Reads robots.txt for an origin, caches it in Redis, and answers one question per URL."""

    def __init__(
        self,
        redis: Redis,
        fetch: RobotsFetcher,
        *,
        user_agent: str = USER_AGENT,
        ttl_seconds: int = CACHE_TTL_SECONDS,
    ) -> None:
        self._redis = redis
        self._fetch = fetch
        self._user_agent = user_agent
        self._ttl = ttl_seconds
        #: Parsed rules by text digest, so repeated pages of one crawl parse once. Keyed by the
        #: digest rather than the origin so two origins serving identical rules share the parse
        #: and a changed file can never be answered from a stale entry.
        self._parsed: dict[str, Protego] = {}
        #: One fetch per origin even when many pages of that origin are checked at once.
        self._locks: dict[str, asyncio.Lock] = {}

    async def decide(self, url: str) -> Decision:
        origin = origin_of(url)
        text = await self._text_for(origin)
        if text == _UNAVAILABLE:
            # RFC 9309: unavailable is a complete disallow. Not knowing the rules is not consent.
            return Decision(False, "robots_unavailable")
        if text == _ABSENT:
            return Decision(True, "robots_absent")

        rules = self._parse(text)
        path = path_of(url)
        delay = rules.crawl_delay(self._user_agent)
        if not rules.can_fetch(path, self._user_agent):
            return Decision(False, "robots_disallow", float(delay) if delay else None)
        return Decision(True, "robots_allow", float(delay) if delay else None)

    def _parse(self, text: str) -> Protego:
        digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:32]
        cached = self._parsed.get(digest)
        if cached is None:
            cached = Protego.parse(text)
            # Bounded: a worker crawling thousands of hosts must not grow a parse per host for
            # ever. Oldest out first; re-parsing is cheap and a wrong answer is not.
            if len(self._parsed) >= 256:
                self._parsed.pop(next(iter(self._parsed)))
            self._parsed[digest] = cached
        return cached

    async def _text_for(self, origin: str) -> str:
        key = f"robots:{origin}"
        try:
            cached = await self._redis.get(key)
        except Exception as exc:
            # A cache we cannot read is a cache miss, not a licence. Fetch and carry on without it.
            log.warning("robots cache unreadable", origin=origin, error=str(exc))
            cached = None
        if cached is not None:
            return cached if isinstance(cached, str) else cached.decode("utf-8", errors="ignore")

        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            # Another page of the same origin may have fetched while this one waited.
            try:
                cached = await self._redis.get(key)
            except Exception:
                cached = None
            if cached is not None:
                return cached if isinstance(cached, str) else cached.decode("utf-8", "ignore")

            text, ttl = await self._fetch_text(origin)
            try:
                await self._redis.set(key, text, ex=ttl)
            except Exception as exc:
                log.warning("robots cache unwritable", origin=origin, error=str(exc))
            return text

    async def _fetch_text(self, origin: str) -> tuple[str, int]:
        url = f"{origin}/robots.txt"
        try:
            status, body = await self._fetch(url)
        except Exception as exc:
            log.warning("robots fetch failed", origin=origin, error=str(exc))
            return _UNAVAILABLE, UNAVAILABLE_TTL_SECONDS

        if 200 <= status < 300:
            return body[:MAX_ROBOTS_BYTES].decode("utf-8", errors="ignore"), self._ttl
        if 400 <= status < 500:
            # No file, so no rules. A 401 or 403 on robots.txt itself is the same statement: the
            # site is not publishing rules to us. The pages themselves still answer for themselves,
            # and a 403 there is caught by the restriction detector.
            return _ABSENT, self._ttl
        log.info("robots unavailable", origin=origin, status=status)
        return _UNAVAILABLE, UNAVAILABLE_TTL_SECONDS
