"""What a model call costs and how that is recorded (docs/07, docs/11).

Two things have to stay true no matter how a call ends: a job cannot spend past its cost cap, and
every token we were billed for reaches `usage_events`. Both are easy to get wrong in the failure
paths, so they live here rather than being scattered through the gateway.
"""

from dataclasses import dataclass, field

import structlog
from redis.asyncio import Redis
from uuid6 import uuid7

from app.ai.config import ModelPrice, price_for
from app.ai.types import ProviderResult, TokenUsage
from app.jobs.errors import BudgetExhaustedError
from app.metering.context import CallContext
from app.metering.usage import UsageRecorder

#: Reserved-then-reconciled model spend for one request, in micros.
SPEND_KEY = "ai:spend:{job_id}"
#: A research job runs for hours and may be retried for days.
SPEND_TTL_S = 7 * 24 * 3600
#: A parse is over in seconds; keeping one key per parse for a week is just litter.
UNJOBBED_SPEND_TTL_S = 300

#: Meter name for model spend in `usage_events` (docs/07).
AI_METER = "ai"

#: Rough characters per token, used only to pre-reserve an upper bound before a call.
CHARS_PER_TOKEN = 3


@dataclass
class Accrual:
    """Running total for one gateway call, including the attempts that produced nothing.

    Deliberately mutable and passed down: a repair turn or a fallback model must add to the same
    total, so that a failure halfway through still reports everything that was spent.
    """

    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_micros: int = 0
    #: What has been reserved against the job's cap but not yet reconciled.
    reserved_micros: int = 0

    def add(self, result: ProviderResult) -> int:
        """Adds one provider response and returns what that response alone cost."""
        cost = price_for(result.model).cost_micros(
            result.usage.input_tokens,
            result.usage.output_tokens,
            result.usage.cache_read_tokens,
            result.usage.cache_write_tokens,
        )
        self.usage = self.usage + result.usage
        self.cost_micros += cost
        return cost

    @property
    def tokens(self) -> int:
        return self.usage.input_tokens + self.usage.output_tokens + self.usage.cache_read_tokens


def estimate_micros(model: str, prompt_chars: int, max_output_tokens: int) -> int:
    """Upper bound for one call: the whole output budget, plus the prompt we are about to send."""
    price: ModelPrice = price_for(model)
    return price.cost_micros(prompt_chars // CHARS_PER_TOKEN + 1, max_output_tokens)


class JobSpend:
    """The per-request model budget (`budget.cost_cap_micros` from the job envelope).

    Keyed on the research job where there is one, and on the envelope's own id where there is
    not, so a parse is capped like anything else and its retries share one counter rather than
    each getting a fresh allowance.
    """

    def __init__(self, redis: Redis, ctx: CallContext) -> None:
        self._redis = redis
        self._ctx = ctx
        self._key = SPEND_KEY.format(job_id=ctx.budget_key)

    async def reserve(self, micros: int, accrual: Accrual) -> None:
        """Claims `micros` against the cap before the call, so parallel calls cannot all pass.

        Raises `BudgetExhaustedError` when the claim would exceed the cap, releasing it first.
        """
        if not self._ctx.budget_key:
            return
        total = await self._incr(micros)
        accrual.reserved_micros += micros
        cap = self._ctx.cost_cap_micros
        if cap > 0 and total > cap:
            await self.release(micros, accrual)
            raise BudgetExhaustedError(f"job would spend {total} of {cap} micros on models")

    async def release(self, micros: int, accrual: Accrual) -> None:
        if not self._ctx.budget_key or micros == 0:
            return
        await self._incr(-micros)
        accrual.reserved_micros -= micros

    async def settle(self, accrual: Accrual) -> None:
        """Replaces what is still reserved with what was actually spent."""
        if not self._ctx.budget_key:
            return
        delta = accrual.cost_micros - accrual.reserved_micros
        if delta:
            await self._incr(delta)
        accrual.reserved_micros = accrual.cost_micros

    async def total(self) -> int:
        raw = await self._redis.get(self._key)
        return int(raw or 0)

    async def _incr(self, micros: int) -> int:
        total = int(await self._redis.incrby(self._key, micros))
        ttl = SPEND_TTL_S if self._ctx.research_job_id else UNJOBBED_SPEND_TTL_S
        await self._redis.expire(self._key, ttl)
        return total


def call_unit_key(cache_key: str, outcome: str = "ok") -> str:
    """A key per *paid* call, not per input.

    Suppressing a repeat of the same question is the response cache's job; by the time a call
    reaches the provider it has been billed, so it gets a row of its own. Keying on the input
    instead would silently drop the second charge whenever the cache is off, expired or disabled
    for the task, leaving `usage_events` short of what the provider actually invoiced.
    """
    return f"{cache_key}:{outcome}:{uuid7()}"


async def record_usage(
    usage: UsageRecorder,
    ctx: CallContext,
    *,
    unit_key: str,
    accrual: Accrual,
    log: structlog.stdlib.BoundLogger,
) -> None:
    """Books what this call cost."""
    if accrual.cost_micros <= 0:
        return
    recorded = await usage.record(
        org_id=ctx.org_id,
        research_job_id=ctx.research_job_id,
        meter=AI_METER,
        unit_key=unit_key,
        cost_micros=accrual.cost_micros,
        units=accrual.tokens,
        # Safe here, and only here: `call_unit_key` ends in a uuid7, so each paid call is its
        # own row whether or not the dedupe table is involved.
        org_level=ctx.research_job_id is None,
    )
    if not recorded:
        # Unexpected now that every paid call carries its own key: it would mean real spend is
        # missing from the ledger while the job's spend counter has already moved.
        log.error(
            "ai usage was not recorded",
            unit_key=unit_key,
            cost_micros=accrual.cost_micros,
            error_class="invalid_input",
        )


async def settle_failure(
    usage: UsageRecorder,
    ctx: CallContext,
    *,
    spend: "JobSpend",
    accrual: Accrual,
    cache_key: str,
    log: structlog.stdlib.BoundLogger,
) -> None:
    """A call that produced nothing usable still cost money; it is recorded as such."""
    await spend.settle(accrual)
    if accrual.cost_micros <= 0:
        return
    await record_usage(
        usage, ctx, unit_key=call_unit_key(cache_key, "failed"), accrual=accrual, log=log
    )
    log.warning("ai call failed after spending", cost_micros=accrual.cost_micros)
