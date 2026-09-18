"""Retries via one delayed ZSET per stream (ADR-0001).

Key `jobs:delayed:{stream}`; score = due time in ms; member = the envelope JSON (unique per job id
and attempt). Promotion is one Lua script that declares both keys it touches.
"""

import random
import time

from redis.asyncio import Redis
from redis.asyncio.client import Pipeline

from app.jobs.envelope import ENVELOPE_FIELD

BASE_DELAY_S = 2.0
MAX_DELAY_S = 5 * 60.0
STREAM_MAXLEN = 100_000

# KEYS[1] = delayed ZSET, KEYS[2] = stream. Moves due members onto the stream atomically.
_PROMOTE_SCRIPT = """
local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, tonumber(ARGV[2]))
for _, envelope in ipairs(due) do
  redis.call('XADD', KEYS[2], 'MAXLEN', '~', ARGV[3], '*', ARGV[4], envelope)
  redis.call('ZREM', KEYS[1], envelope)
end
return #due
"""


def delayed_key(stream: str) -> str:
    return f"jobs:delayed:{stream}"


def backoff_seconds(
    attempt: int, retry_after_s: float | None = None, *, base_s: float = BASE_DELAY_S
) -> float:
    """Exponential backoff with full jitter; honours Retry-After when larger."""
    exponential = min(MAX_DELAY_S, base_s * (2 ** max(0, attempt - 1)))
    delay = random.uniform(exponential / 2, exponential)  # noqa: S311 - jitter, not crypto
    if retry_after_s is not None:
        delay = max(delay, retry_after_s)
    return delay


def queue_retry(pipe: Pipeline, stream: str, envelope_json: str, delay_s: float) -> None:
    """Adds the ZADD to a MULTI pipeline so it commits together with the XACK of the old message."""
    due_ms = int((time.time() + delay_s) * 1000)
    pipe.zadd(delayed_key(stream), {envelope_json: due_ms})


async def promote_due(redis: Redis, stream: str, batch: int = 100) -> int:
    now_ms = int(time.time() * 1000)
    promoted = await redis.eval(
        _PROMOTE_SCRIPT,
        2,
        delayed_key(stream),
        stream,
        now_ms,
        batch,
        STREAM_MAXLEN,
        ENVELOPE_FIELD,
    )
    return int(promoted)
