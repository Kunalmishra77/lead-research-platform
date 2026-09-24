"""The ceiling on what one request may spend, and the cancel check that stops it (task 2.11).

These sit in the shared HTTP client because `cost_cap_micros` is a total callers must not
decrement: only something that sees every call can know what is left. What is worth pinning is
the money, not the mechanism — can a request spend past its cap, can it lose budget to a call
that never landed, and can a cancelled job still buy anything.
"""

import asyncio

import fakeredis
import httpx
import pytest
import respx

from app.connectors.http_client import CallCost, ConnectorHttpClient
from app.connectors.types import RateLimit
from app.jobs.cancellation import CANCEL_KEY
from app.jobs.errors import BudgetExhaustedError, JobError
from app.metering.budget import SPEND_KEY, SpendLedger
from tests.connector_support import USER_AGENT, RecordingUsage, make_ctx

URL = "https://api.example.test/search"
COST = 35_000


def make_client(redis: object, usage: RecordingUsage | None = None) -> ConnectorHttpClient:
    return ConnectorHttpClient(
        source_key="example_source",
        rate_limit=RateLimit(requests=100, per_seconds=1.0, concurrency=8),
        user_agent=USER_AGENT,
        usage=usage or RecordingUsage(),
        redis=redis,  # type: ignore[arg-type]
        client=httpx.AsyncClient(timeout=5.0),
    )


@respx.mock
async def test_a_request_cannot_spend_past_its_cap() -> None:
    sent = respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    redis = fakeredis.FakeAsyncRedis()
    ctx = make_ctx(cost_cap_micros=COST * 2)
    client = make_client(redis)
    try:
        await client.get(URL, cost=CallCost(meter="api_example", cost_micros=COST), ctx=ctx)
        await client.get(URL, cost=CallCost(meter="api_example", cost_micros=COST), ctx=ctx)
        with pytest.raises(BudgetExhaustedError, match="micros allowed"):
            await client.get(URL, cost=CallCost(meter="api_example", cost_micros=COST), ctx=ctx)
    finally:
        await client.aclose()
        await redis.aclose()

    # The third call is refused before it is made, not after it is paid for.
    assert sent.call_count == 2


@respx.mock
async def test_parallel_calls_cannot_all_read_a_total_under_the_cap() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    redis = fakeredis.FakeAsyncRedis()
    ctx = make_ctx(cost_cap_micros=COST * 3)
    client = make_client(redis)

    async def one() -> bool:
        try:
            await client.get(URL, cost=CallCost(meter="api_example", cost_micros=COST), ctx=ctx)
        except BudgetExhaustedError:
            return False
        return True

    try:
        results = await asyncio.gather(*(one() for _ in range(10)))
    finally:
        await client.aclose()

    # Claimed before the call rather than recorded after it: recording after would let all ten
    # read a total under the cap at the same moment and all spend.
    assert sum(results) == 3
    assert int(await redis.get(SPEND_KEY.format(scope=ctx.budget_key))) == COST * 3
    await redis.aclose()


@respx.mock
async def test_a_call_that_was_not_billed_gives_its_claim_back() -> None:
    respx.get(URL).mock(return_value=httpx.Response(400, json={"error": "bad query"}))
    redis = fakeredis.FakeAsyncRedis()
    ctx = make_ctx(cost_cap_micros=COST * 2)
    client = make_client(redis)
    try:
        await client.get(URL, cost=CallCost(meter="api_example", cost_micros=COST), ctx=ctx)
    finally:
        await client.aclose()

    # Providers charge for answers. A job that lost budget to every rejected request would run
    # out of money having bought nothing.
    assert int(await redis.get(SPEND_KEY.format(scope=ctx.budget_key)) or 0) == 0
    await redis.aclose()


@respx.mock
async def test_a_call_that_never_landed_does_not_cost_the_job_its_budget() -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("no route"))
    redis = fakeredis.FakeAsyncRedis()
    ctx = make_ctx(cost_cap_micros=COST * 2)
    client = make_client(redis)
    try:
        with pytest.raises(JobError):
            await client.get(URL, cost=CallCost(meter="api_example", cost_micros=COST), ctx=ctx)
    finally:
        await client.aclose()

    # The claim is released on the way out of a raise too, or a flaky provider would drain a
    # budget without ever answering.
    assert int(await redis.get(SPEND_KEY.format(scope=ctx.budget_key)) or 0) == 0
    await redis.aclose()


@respx.mock
async def test_a_cancelled_job_buys_nothing_more() -> None:
    sent = respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    redis = fakeredis.FakeAsyncRedis()
    ctx = make_ctx(cost_cap_micros=COST * 10)
    await redis.set(CANCEL_KEY.format(job_id=ctx.research_job_id), "1")
    client = make_client(redis)
    try:
        # ADR-0008's third checkpoint, placed before the call rather than before the usage row:
        # by the time that row is written the provider has already been paid, so checking there
        # would only lose our record of real spend instead of preventing it.
        with pytest.raises(Exception, match="cancelled"):
            await client.get(URL, cost=CallCost(meter="api_example", cost_micros=COST), ctx=ctx)
    finally:
        await client.aclose()

    assert sent.call_count == 0
    await redis.aclose()


@respx.mock
async def test_a_free_call_is_not_stopped_by_a_cancelled_job() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    redis = fakeredis.FakeAsyncRedis()
    ctx = make_ctx(cost_cap_micros=COST)
    await redis.set(CANCEL_KEY.format(job_id=ctx.research_job_id), "1")
    client = make_client(redis)
    try:
        # A health probe or a robots.txt fetch costs nothing, and refusing it would only make
        # winding a cancelled job down harder.
        response = await client.get(URL, cost=CallCost(meter="api_example"), ctx=ctx)
    finally:
        await client.aclose()

    assert response.status_code == 200
    await redis.aclose()


@respx.mock
async def test_an_uncapped_request_is_not_silently_capped() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    redis = fakeredis.FakeAsyncRedis()
    ctx = make_ctx(cost_cap_micros=0)
    client = make_client(redis)
    try:
        for _ in range(5):
            await client.get(URL, cost=CallCost(meter="api_example", cost_micros=COST), ctx=ctx)
    finally:
        await client.aclose()

    # Zero means uncapped everywhere else in the system; inventing a ceiling here would stop
    # work nobody limited.
    assert await redis.get(SPEND_KEY.format(scope=ctx.budget_key)) is None
    await redis.aclose()


async def test_the_ledger_is_scoped_to_the_envelope_not_the_research_job() -> None:
    redis = fakeredis.FakeAsyncRedis()
    ctx = make_ctx(cost_cap_micros=COST)
    ledger = SpendLedger(redis, ctx)
    await ledger.reserve(COST)

    # The cap on a context belongs to one task since the planner began fanning a job out with a
    # share of its budget each. Counting the whole job against one task's cap would fail every
    # task after the first.
    assert ctx.budget_key == ctx.spend_id
    assert int(await redis.get(SPEND_KEY.format(scope=ctx.spend_id))) == COST
    await redis.aclose()
