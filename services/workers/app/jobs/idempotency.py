"""Exactly-once effects on top of at-least-once delivery (docs/06 idempotency).

Key `idem:{scope}:{idempotency_key}` (scope = org id or "global"):
- `processing:{message_id}` while a worker runs the job. The token is the stream message id, so a
  reclaim of the *same* message after a crash takes the job over immediately, while a duplicate
  message of a job that is still running is recognised as a duplicate. The TTL is refreshed by the
  consumer's heartbeat and only exists to clean up after crashes.
- `done` after success; later deliveries are acknowledged without running again.
"""

from typing import Literal

from redis.asyncio import Redis

DONE_TTL_S = 7 * 24 * 60 * 60

Claim = Literal["acquired", "done", "busy"]

# Refresh the TTL only while we still own the key.
_REFRESH = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
return 0
"""
# Delete only if we still own the key (never drop another worker's claim or a "done" marker).
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def idempotency_key(scope: str | None, key: str) -> str:
    return f"idem:{scope or 'global'}:{key}"


def _token(message_id: str) -> str:
    return f"processing:{message_id}"


class IdempotencyGuard:
    def __init__(self, redis: Redis, processing_ttl_s: int) -> None:
        self._redis = redis
        self._ttl = processing_ttl_s

    async def claim(self, key: str, message_id: str) -> Claim:
        token = _token(message_id)
        if await self._redis.set(key, token, ex=self._ttl, nx=True):
            return "acquired"
        state = await self._redis.get(key)
        value = state.decode() if isinstance(state, bytes) else state
        if value == "done":
            return "done"
        if value == token:
            # Same message reclaimed after its worker died: take the job over.
            await self._redis.expire(key, self._ttl)
            return "acquired"
        if value is None:
            acquired = await self._redis.set(key, token, ex=self._ttl, nx=True)
            return "acquired" if acquired else "busy"
        return "busy"

    async def heartbeat(self, key: str, message_id: str) -> None:
        await self._redis.eval(_REFRESH, 1, key, _token(message_id), self._ttl)

    async def complete(self, key: str) -> None:
        await self._redis.set(key, "done", ex=DONE_TTL_S)

    async def release(self, key: str, message_id: str) -> None:
        """After a failure: let the retry claim the key again."""
        await self._redis.eval(_RELEASE, 1, key, _token(message_id))
