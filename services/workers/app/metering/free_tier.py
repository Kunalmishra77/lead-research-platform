"""The guard that keeps a provider's free allowance from being crossed by accident.

`SpendLedger` bounds what one task spends against the budget a customer paid for. This bounds
something different and, for an operator with no budget at all, more important: what *we* are
billed by a provider. Google's Places free allowance resets on the first of the month and is
counted per SKU; the call after the last free one costs $35 per thousand, with no warning and no
different response. Nothing in the product would notice.

So the count lives here, keyed by provider SKU and calendar month, and a call past the limit is
refused with `budget_exhausted` -- the same class a job over its credit budget gets, because it is
the same shape of answer: stop, do not retry, and say so.

**Counted before the call, like the spend ledger, and for the same reason.** Counting afterwards
lets every task of a fanned-out job read a total under the limit in the same moment and all go
past it together.

**The reservation is given back when a call turns out not to have happened.** A request that failed
to leave the machine was not billed, and holding its slot would shrink a free allowance we had not
actually used.

**The limit is deliberately below the real one.** A provider's counter and ours will not agree
exactly -- a retried request may or may not have reached them, and their month boundary is in their
timezone, not ours. The headroom is what makes "no spend" a fact rather than an arithmetic hope.
"""

from datetime import UTC, datetime

import structlog
from redis.asyncio import Redis

from app.jobs.errors import BudgetExhaustedError

log = structlog.get_logger(__name__)

#: One key per SKU per calendar month: `freetier:google_places_text_search:2026-10`.
FREE_TIER_KEY = "freetier:{sku}:{month}"

#: Two months, so the previous month's number can still be read when a question is asked on the
#: first. Longer would keep counters nobody will look at.
KEY_TTL_S = 62 * 24 * 3600


class FreeTierGuard:
    """How many billable calls a SKU has left this month, shared across every worker."""

    def __init__(self, redis: Redis, *, sku: str, monthly_limit: int, headroom: int = 0) -> None:
        if monthly_limit < 0:
            raise ValueError("monthly_limit cannot be negative")
        self._redis = redis
        self._sku = sku
        #: What we will actually allow. See the module docstring on why it is not the real limit.
        self._limit = max(monthly_limit - headroom, 0)

    def key(self, *, now: datetime | None = None) -> str:
        when = now or datetime.now(UTC)
        return FREE_TIER_KEY.format(sku=self._sku, month=when.strftime("%Y-%m"))

    async def used(self, *, now: datetime | None = None) -> int:
        raw = await self._redis.get(self.key(now=now))
        return int(raw) if raw else 0

    async def remaining(self, *, now: datetime | None = None) -> int:
        return max(self._limit - await self.used(now=now), 0)

    async def reserve(self, calls: int = 1, *, now: datetime | None = None) -> None:
        """Claims `calls` of this month's allowance, or raises `BudgetExhaustedError`.

        Raises rather than returning False: a caller that could ignore the answer would, and the
        error class is the one the consumer already knows not to retry.
        """
        if calls <= 0:
            return
        key = self.key(now=now)
        total = int(await self._redis.incrby(key, calls))
        if total == calls:
            # First call of the month: give the counter a life, so an abandoned month expires.
            await self._redis.expire(key, KEY_TTL_S)
        if total > self._limit:
            await self._redis.decrby(key, calls)
            log.warning(
                "free tier exhausted",
                sku=self._sku,
                limit=self._limit,
                used=total - calls,
                error_class="budget_exhausted",
            )
            raise BudgetExhaustedError(
                f"{self._sku}: this month's free allowance of {self._limit} calls is used up; "
                f"the next call would be billed, so it was not made"
            )

    async def release(self, calls: int = 1, *, now: datetime | None = None) -> None:
        """Gives back a reservation for a call that did not happen, so it stays available."""
        if calls <= 0:
            return
        key = self.key(now=now)
        remaining = int(await self._redis.decrby(key, calls))
        if remaining < 0:
            # Only reachable if a release outnumbers its reserves, which would be a bug elsewhere.
            # Clamped rather than left negative: a negative count would hand out free calls.
            await self._redis.set(key, 0, ex=KEY_TTL_S)


class NullFreeTierGuard:
    """No limit. For a paid account, and for tests that are not about the allowance."""

    async def used(self, *, now: datetime | None = None) -> int:
        return 0

    async def remaining(self, *, now: datetime | None = None) -> int:
        return 2**31

    async def reserve(self, calls: int = 1, *, now: datetime | None = None) -> None:
        return None

    async def release(self, calls: int = 1, *, now: datetime | None = None) -> None:
        return None
