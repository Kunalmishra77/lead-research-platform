"""Replying to a synchronous request that came in over the job stream (ADR-0005).

The API generates a reply key, publishes an envelope and blocks on that key. A worker pushes
exactly one reply. `LPUSH` + `EXPIRE` is the whole protocol: it gives the waiter clean timeout
semantics and leaves nothing behind when nobody is waiting.

A failure is a reply too. Staying silent would make every error look like a 20-second timeout.
"""

import json
from typing import Any

from redis.asyncio import Redis

from app.jobs.errors import InvalidInputError, classify
from app.jobs.redact import redact

#: Only keys under this prefix may be written to. The API generates the key, but a worker still
#: checks it: a reply key taken from a request payload would be a write primitive for anyone who
#: can enqueue a job.
REPLY_PREFIX = "rpc:reply:"

#: The waiter is gone after its own timeout; this only has to outlive that.
REPLY_TTL_S = 120


def reply_key(job_id: str) -> str:
    return f"{REPLY_PREFIX}{job_id}"


def check_reply_key(key: str, job_id: str) -> str:
    """Refuses a reply key that is not the one this job is entitled to write."""
    expected = reply_key(job_id)
    if key != expected:
        raise InvalidInputError("reply_to does not belong to this job")
    return key


async def send_reply(redis: Redis, key: str, payload: dict[str, Any]) -> None:
    if not key.startswith(REPLY_PREFIX):
        raise InvalidInputError(f"refusing to write outside {REPLY_PREFIX}")
    async with redis.pipeline(transaction=True) as pipe:
        pipe.lpush(key, json.dumps(payload, ensure_ascii=False))
        pipe.expire(key, REPLY_TTL_S)
        await pipe.execute()


async def send_failure(redis: Redis, key: str, job_id: str, exc: BaseException) -> None:
    """Classifies the failure so the caller can map it to a status rather than guessing.

    The text reaches an end user through the API, so it goes through the same redaction as
    every other escaping error (docs/10): a provider exception routinely carries the request
    URL, and sometimes more.
    """
    message = redact(str(exc) or type(exc).__name__)
    await send_reply(
        redis,
        key,
        {
            "reply_version": 1,
            "job_id": job_id,
            "ok": False,
            "error": {"error_class": classify(exc).value, "message": message},
        },
    )
