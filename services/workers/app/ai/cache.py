"""Response cache for model calls (docs/07).

Key = sha256(task, prompt version, model, normalized input). Identical work inside the TTL is
answered from Redis instead of the provider: the same research run asks the same questions often,
and a cached answer costs nothing and cannot drift.

Keys are namespaced per org. A shared cache would have a better hit rate, but the input that
produced an answer is tenant data, and CLAUDE.md does not make exceptions for derived values.
"""

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from redis.asyncio import Redis

from app.ai.types import AiResult, TokenUsage

KEY_PREFIX = "ai:cache:"


def cache_key(
    *,
    org_id: str,
    task: str,
    prompt_version: str,
    model: str,
    max_output_tokens: int,
    payload: Any,
) -> str:
    """Stable across processes: sorted keys, no whitespace, explicit separators.

    The token budget is part of the key because raising it can turn a truncated answer into a
    complete one, and the old answer must not be served in its place.
    """
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    parts = (org_id, task, prompt_version, model, str(max_output_tokens), normalized)
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{KEY_PREFIX}{org_id}:{task}:{digest}"


class AiResponseCache:
    def __init__(self, redis: Redis, *, enabled: bool = True) -> None:
        self._redis = redis
        self._enabled = enabled

    async def get(self, key: str) -> AiResult | None:
        if not self._enabled:
            return None
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            stored = json.loads(raw)
            return AiResult(
                task=stored["task"],
                data=stored["data"],
                model=stored["model"],
                prompt_version=stored["prompt_version"],
                origin="cache",
                usage=TokenUsage(),
                cost_micros=0,
                # The original observation time, so a stored value cannot claim to be fresh.
                observed_at=datetime.fromisoformat(stored["observed_at"]).astimezone(UTC),
            )
        except (ValueError, KeyError, TypeError):
            # A malformed entry is not worth a failed job; drop it and call the provider.
            await self._redis.delete(key)
            return None

    async def set(self, key: str, result: AiResult, ttl_s: int) -> None:
        if not self._enabled or ttl_s <= 0:
            return
        payload = json.dumps(
            {
                "task": result.task,
                "data": result.data,
                "model": result.model,
                "prompt_version": result.prompt_version,
                "observed_at": result.observed_at.isoformat(),
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        await self._redis.set(key, payload, ex=ttl_s)
