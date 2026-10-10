"""A request rate shared by every worker, because a provider's quota is.

`AsyncLimiter` bounds one process. A connector is built per task, so four discovery tasks on one
worker each got their own ten-per-second and sent forty, and a second worker would have doubled
that again. docs/08 says the opposite and says why: "limits are per connector, because a quota
belongs to the API key, not the hostname". An API key does not know how many of our processes
exist.

What that cost, measured: an area-wide Places sweep made sixty-eight calls in its first fifty
seconds, collected `429`s, and then sat in `Retry-After` backoff for twenty-four minutes without
sending anything. Not a crash and not a slow job — a job that had stopped while still saying
`running`.

So the bucket lives in Redis, keyed by source. A token bucket rather than a fixed window: a
window lets a whole second's worth of requests leave in the same millisecond at the boundary,
which is exactly the burst a provider answers with `429`.
"""

import asyncio
from dataclasses import dataclass

import structlog
from redis.asyncio import Redis

log = structlog.get_logger(__name__)

#: One bucket per source: `ratelimit:google_places`.
BUCKET_KEY = "ratelimit:{source}"

#: Idle buckets expire. A minute is longer than any sane refill and short enough that a key for
#: a source nobody calls does not outlive the deploy that used it.
BUCKET_TTL_S = 60

#: How long a caller may wait inside one `acquire()` before giving up on politeness and letting
#: the request through. Reached only if the bucket is starved for this long, which means the
#: configured rate cannot keep up with demand -- a tuning problem, not something to deadlock on.
MAX_WAIT_S = 30.0

#: Returns milliseconds to wait, or 0 when a token was taken.
#:
#: KEYS[1] bucket   ARGV[1] tokens/sec   ARGV[2] burst   ARGV[3] ttl_s
#:
#: The whole decision is one script so that reading the level, refilling it and taking a token
#: cannot interleave with another worker doing the same -- which is the only way two processes
#: both see "one token left" and both take it.
#:
#: The clock is Redis's own `TIME`, not the caller's. A monotonic clock is per-process and two
#: workers' versions of "now" have no relationship at all, so a bucket refilled from them would
#: lurch backwards and forwards and hand out tokens it did not have.
_TAKE = """
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local clock = redis.call('TIME')
local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)

local state = redis.call('HMGET', KEYS[1], 'tokens', 'at')
local tokens = tonumber(state[1])
local at = tonumber(state[2])
if tokens == nil or at == nil then
  tokens = burst
  at = now
end

-- Refill for the time that passed, never above the burst ceiling.
local gained = (now - at) * rate / 1000.0
tokens = math.min(burst, tokens + gained)

if tokens >= 1 then
  tokens = tokens - 1
  redis.call('HSET', KEYS[1], 'tokens', tokens, 'at', now)
  redis.call('EXPIRE', KEYS[1], ttl)
  return 0
end

-- Not enough yet: say when one more will have arrived, and change nothing.
redis.call('HSET', KEYS[1], 'tokens', tokens, 'at', now)
redis.call('EXPIRE', KEYS[1], ttl)
return math.ceil((1 - tokens) * 1000.0 / rate)
"""


@dataclass(frozen=True)
class Rate:
    """Requests per second, and how many may leave at once after an idle spell."""

    per_second: float
    burst: int

    @classmethod
    def of(cls, requests: int, per_seconds: float, *, burst: int | None = None) -> "Rate":
        if requests <= 0 or per_seconds <= 0:
            raise ValueError("a rate needs a positive number of requests and a positive window")
        rate = requests / per_seconds
        # A burst of one window's worth: enough that a quiet source answers immediately, little
        # enough that an idle hour does not buy an hour's requests to spend in one go.
        return cls(per_second=rate, burst=burst if burst is not None else max(1, requests))


class SharedRateLimiter:
    """The rate a source is called at, counted across every worker that calls it."""

    def __init__(self, redis: Redis, *, source: str, rate: Rate) -> None:
        self._redis = redis
        self._key = BUCKET_KEY.format(source=source)
        self._source = source
        self._rate = rate

    async def acquire(self) -> None:
        """Waits until this process may send one request."""
        waited = 0.0
        while True:
            wait_ms = int(
                await self._redis.eval(
                    _TAKE,
                    1,
                    self._key,
                    str(self._rate.per_second),
                    str(self._rate.burst),
                    str(BUCKET_TTL_S),
                )
            )
            if wait_ms <= 0:
                return
            delay = min(wait_ms / 1000.0, MAX_WAIT_S - waited)
            if delay <= 0:
                # Starved for MAX_WAIT_S. Letting it through beats hanging: the provider's own
                # 429 is a better place to find out than a worker that never returns.
                log.warning(
                    "rate limiter waited its maximum and let a request through",
                    source=self._source,
                    waited_s=round(waited, 1),
                )
                return
            await asyncio.sleep(delay)
            waited += delay

    async def __aenter__(self) -> "SharedRateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *_: object) -> None:
        return None
