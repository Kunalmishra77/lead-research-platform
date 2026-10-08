"""The free-allowance guard.

The point of it: an operator with no budget must not be able to cross a provider's free tier by
running one search too many. Google's Places Enterprise allowance is 1,000 calls a month and the
call after the last free one costs $35 per thousand, with no different response and no warning.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis

from app.jobs.errors import BudgetExhaustedError
from app.metering.free_tier import FreeTierGuard, NullFreeTierGuard

SKU = "google_places_text_search"
OCTOBER = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
NOVEMBER = datetime(2026, 11, 1, 0, 30, tzinfo=UTC)


def guard(redis: Redis, *, limit: int = 1000, headroom: int = 0) -> FreeTierGuard:
    return FreeTierGuard(redis, sku=SKU, monthly_limit=limit, headroom=headroom)


def test_the_key_is_per_sku_and_per_calendar_month(redis: Redis) -> None:
    assert guard(redis).key(now=OCTOBER) == f"freetier:{SKU}:2026-10"
    assert guard(redis).key(now=NOVEMBER) == f"freetier:{SKU}:2026-11"


def test_a_negative_limit_is_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        FreeTierGuard(None, sku=SKU, monthly_limit=-1)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_calls_inside_the_allowance_are_allowed_and_counted(redis: Redis) -> None:
    subject = guard(redis, limit=3)
    for _ in range(3):
        await subject.reserve(now=OCTOBER)
    assert await subject.used(now=OCTOBER) == 3
    assert await subject.remaining(now=OCTOBER) == 0


@pytest.mark.asyncio
async def test_the_call_that_would_be_billed_is_not_made(redis: Redis) -> None:
    subject = guard(redis, limit=2)
    await subject.reserve(now=OCTOBER)
    await subject.reserve(now=OCTOBER)
    with pytest.raises(BudgetExhaustedError, match="free allowance"):
        await subject.reserve(now=OCTOBER)
    # And the refusal did not consume anything: the count is still the two that were allowed.
    assert await subject.used(now=OCTOBER) == 2


@pytest.mark.asyncio
async def test_headroom_stops_short_of_the_real_limit(redis: Redis) -> None:
    # Our count and the provider's will not agree exactly -- a retried request may or may not have
    # reached them, and their month starts in their timezone. The headroom is what makes "no spend"
    # a fact rather than an arithmetic hope.
    subject = guard(redis, limit=100, headroom=10)
    assert await subject.remaining(now=OCTOBER) == 90
    for _ in range(90):
        await subject.reserve(now=OCTOBER)
    with pytest.raises(BudgetExhaustedError):
        await subject.reserve(now=OCTOBER)


@pytest.mark.asyncio
async def test_a_new_month_starts_a_new_allowance(redis: Redis) -> None:
    subject = guard(redis, limit=1)
    await subject.reserve(now=OCTOBER)
    with pytest.raises(BudgetExhaustedError):
        await subject.reserve(now=OCTOBER)
    # Google's allowance resets on the first; so does ours, because the key carries the month.
    await subject.reserve(now=NOVEMBER)
    assert await subject.used(now=NOVEMBER) == 1
    assert await subject.used(now=OCTOBER) == 1


@pytest.mark.asyncio
async def test_a_call_that_did_not_happen_gives_its_slot_back(redis: Redis) -> None:
    # A request that never left the machine was not billed, and holding its slot would shrink an
    # allowance we had not used.
    subject = guard(redis, limit=2)
    await subject.reserve(now=OCTOBER)
    await subject.release(now=OCTOBER)
    assert await subject.used(now=OCTOBER) == 0
    assert await subject.remaining(now=OCTOBER) == 2


@pytest.mark.asyncio
async def test_the_count_never_goes_negative(redis: Redis) -> None:
    # Only reachable through a bug elsewhere, and a negative count would hand out free calls.
    subject = guard(redis, limit=5)
    await subject.release(now=OCTOBER)
    assert await subject.used(now=OCTOBER) == 0
    assert await subject.remaining(now=OCTOBER) == 5


@pytest.mark.asyncio
async def test_a_zero_or_negative_reservation_does_nothing(redis: Redis) -> None:
    subject = guard(redis, limit=1)
    await subject.reserve(0, now=OCTOBER)
    await subject.reserve(-5, now=OCTOBER)
    await subject.release(0, now=OCTOBER)
    assert await subject.used(now=OCTOBER) == 0


@pytest.mark.asyncio
async def test_the_counter_is_given_a_life_so_old_months_expire(redis: Redis) -> None:
    subject = guard(redis, limit=5)
    await subject.reserve(now=OCTOBER)
    assert await redis.ttl(subject.key(now=OCTOBER)) > 0


@pytest.mark.asyncio
async def test_a_zero_limit_allows_nothing(redis: Redis) -> None:
    # The switch for "this provider is off": clearer than a flag somewhere else.
    with pytest.raises(BudgetExhaustedError):
        await guard(redis, limit=0).reserve(now=OCTOBER)


@pytest.mark.asyncio
async def test_workers_sharing_the_counter_cannot_both_take_the_last_call(redis: Redis) -> None:
    subject = guard(redis, limit=1)
    results = await asyncio.gather(
        subject.reserve(now=OCTOBER),
        subject.reserve(now=OCTOBER),
        return_exceptions=True,
    )
    refused = [r for r in results if isinstance(r, BudgetExhaustedError)]
    assert len(refused) == 1
    assert await subject.used(now=OCTOBER) == 1


@pytest.mark.asyncio
async def test_the_null_guard_never_refuses(redis: Redis) -> None:
    subject = NullFreeTierGuard()
    for _ in range(5):
        await subject.reserve()
    await subject.release()
    assert await subject.used() == 0
    assert await subject.remaining() > 0
