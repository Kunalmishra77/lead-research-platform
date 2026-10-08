"""The crawl frontier (docs/06 sections 4.1 and 4.3, task 3.1).

Two sorted sets, as docs/06 lays out:

* `crawl:hosts` -- member a host, score the millisecond it may next be fetched. This is the
  politeness clock, and it is the whole reason the frontier is in Redis rather than in a process:
  `HostPacer` keeps one worker from hammering a site, and only a shared clock keeps four
  workers from doing it between them.
* `crawl:urls:{host}` -- member a URL, score its priority, lowest first. A contact page is
  worth more than a careers page and both are worth more than whatever a sitemap offered.

**Claiming is one Lua script, and that is what makes per-host concurrency 1 true.** The script
finds the earliest host that is due, pops its best URL, and pushes that host's clock forward before
returning -- so a second worker arriving in the same millisecond sees the host as not due and moves
on. Doing it in three round trips instead would let both workers fetch the same site at once, which
is the single thing the politeness rule exists to prevent.

**A host stays in the clock after its last URL is taken.** Removing it there would drop its
cooldown, and a link to that host discovered a moment later would be fetched with no delay at all.
The next claim that reaches an empty host is what removes it.

The seen set is per job and expires: a job must not fetch one page twice, and a job next month
must not inherit this one's memory of having already done so.
"""

import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import structlog
from redis.asyncio import Redis

log = structlog.get_logger(__name__)

HOSTS_KEY = "crawl:hosts"
URLS_PREFIX = "crawl:urls:"
SEEN_PREFIX = "crawl:seen:"

#: docs/06 section 4.3: one request per host at a time, a second or two apart.
DEFAULT_COOLDOWN_S = 1.5

#: A job's memory of what it has queued. Long enough to outlive any single crawl, short enough
#: that the next crawl of the same site is a fresh look rather than a continuation.
SEEN_TTL_S = 7 * 24 * 60 * 60

#: How many hosts one claim will look past. A host whose URL set has been drained is removed and
#: the next is tried; without a bound, a frontier full of emptied hosts would spin.
_CLAIM_SCAN = 20

# KEYS[1] = hosts ZSET. ARGV[1] = now ms, ARGV[2] = cooldown ms, ARGV[3] = urls key prefix.
#
# The per-host URL key is built inside the script from the prefix, so it is not declared in KEYS.
# That is fine on a single Redis and would need fixing for Redis Cluster, where every key a script
# touches must hash to the same slot; ADR-0002 puts us on one managed instance.
_CLAIM = """
local now = tonumber(ARGV[1])
local cooldown = tonumber(ARGV[2])
for _ = 1, tonumber(ARGV[4]) do
  local ready = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', now, 'LIMIT', 0, 1)
  if #ready == 0 then return nil end
  local host = ready[1]
  local urls = ARGV[3] .. host
  local popped = redis.call('ZPOPMIN', urls)
  if #popped == 0 then
    -- Drained: nothing left to fetch here, so the host leaves the clock too.
    redis.call('ZREM', KEYS[1], host)
  else
    -- Pushed forward before returning, so a worker arriving now sees this host as not due.
    redis.call('ZADD', KEYS[1], now + cooldown, host)
    return {host, popped[1]}
  end
end
return nil
"""


@dataclass(frozen=True, slots=True)
class Claimed:
    """One URL, claimed for fetching. The host's clock is already pushed forward."""

    host: str
    url: str


def host_of(url: str) -> str:
    """The netloc a politeness clock is kept against: host and port, lowercased."""
    return urlsplit(url).netloc.lower()


def urls_key(host: str) -> str:
    return f"{URLS_PREFIX}{host}"


def seen_key(job_id: str) -> str:
    return f"{SEEN_PREFIX}{job_id}"


class Frontier:
    """Shared queue of URLs to crawl, ordered by priority and paced per host."""

    def __init__(
        self,
        redis: Redis,
        *,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        seen_ttl_s: int = SEEN_TTL_S,
    ) -> None:
        self._redis = redis
        self._cooldown_ms = int(cooldown_s * 1000)
        self._seen_ttl = seen_ttl_s

    async def add(self, job_id: str, urls: list[tuple[str, float]]) -> int:
        """Queues URLs for a job, skipping any it has queued before. Returns how many were new.

        The URL is queued before it is marked seen, not after. A crash between the two leaves a URL
        queued and unmarked, which costs a duplicate ZADD of the same member -- that is, nothing,
        because the member is the URL. The other order would lose the page entirely.
        """
        if not urls:
            return 0
        seen = seen_key(job_id)
        # One round trip for the whole batch. Asking per URL would make a six-page crawl six
        # sequential calls to answer a question Redis can answer in one.
        members = [url for url, _ in urls]
        already = await self._redis.smismember(seen, members)
        fresh = [
            (url, priority)
            for (url, priority), seen_before in zip(urls, already, strict=True)
            if not seen_before
        ]
        if not fresh:
            return 0

        now_ms = int(time.time() * 1000)
        pipe = self._redis.pipeline(transaction=True)
        hosts: set[str] = set()
        for url, priority in fresh:
            host = host_of(url)
            if not host:
                continue
            pipe.zadd(urls_key(host), {url: priority})
            hosts.add(host)
        for host in hosts:
            # `nx=True`: a host already in the clock keeps its score. Overwriting it with `now`
            # would hand a cooling-down site a free immediate fetch every time a link to it turned
            # up, which is how a polite crawler stops being one.
            pipe.zadd(HOSTS_KEY, {host: now_ms}, nx=True)
        pipe.sadd(seen, *[url for url, _ in fresh])
        pipe.expire(seen, self._seen_ttl)
        await pipe.execute()
        return len(fresh)

    async def claim(self, *, cooldown_s: float | None = None) -> Claimed | None:
        """The next URL that may be fetched now, or None when every host is cooling down.

        None does not mean the frontier is empty: `pending()` answers that. A caller should wait
        and ask again.
        """
        cooldown_ms = self._cooldown_ms if cooldown_s is None else int(cooldown_s * 1000)
        result = await self._redis.eval(
            _CLAIM,
            1,
            HOSTS_KEY,
            int(time.time() * 1000),
            cooldown_ms,
            URLS_PREFIX,
            _CLAIM_SCAN,
        )
        if not result:
            return None
        host, url = (_text(result[0]), _text(result[1]))
        return Claimed(host, url)

    async def defer(self, host: str, delay_s: float) -> None:
        """Pushes one host's clock further out, for a robots.txt `Crawl-delay` longer than ours."""
        due = int((time.time() + delay_s) * 1000)
        # GT so a longer wait already set by another worker is not shortened by this one.
        await self._redis.zadd(HOSTS_KEY, {host: due}, gt=True)

    async def pending(self, host: str | None = None) -> int:
        """URLs still queued: for one host, or across every host in the clock."""
        if host is not None:
            return int(await self._redis.zcard(urls_key(host)))
        hosts = await self._redis.zrange(HOSTS_KEY, 0, -1)
        total = 0
        for raw in hosts:
            total += int(await self._redis.zcard(urls_key(_text(raw))))
        return total

    async def hosts(self) -> int:
        """How many hosts the clock is tracking, due or not."""
        return int(await self._redis.zcard(HOSTS_KEY))

    async def forget(self, job_id: str) -> None:
        """Drops a job's memory of what it queued, so the same pages can be crawled again."""
        await self._redis.delete(seen_key(job_id))


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
