"""The running total one request has spent, and the ceiling it may not cross (docs/11).

`cost_cap_micros` is a total, not a remainder: callers must not decrement it (`context.py`). So
something has to remember what has already gone, and it has to be somewhere every process can
see, because one research job's tasks run in parallel across workers. That is this: a Redis
counter per envelope, claimed before a call and given back when the call turns out not to cost
anything.

**One counter for models and APIs alike.** A task's cap is a single number covering everything
that task does. Two counters — one for the gateway, one for the connectors — would let it spend
the cap twice, once on each.

**Per envelope, not per job.** It is tempting to count a whole research job in one place, but the
cap on the envelope is the *task's* share, so a job-wide total would cross any one task's cap
almost immediately and every task after the first would fail as `budget_exhausted`. The job-wide
bound is structural instead: the planner divides the job's reserved credits between its tasks
(`app/planner/plan.py`), so the sum of the caps cannot exceed what was reserved, and enforcing
each one enforces the whole. Retries of one envelope share its counter, which is right — the
first attempt's spend really did happen.
"""

from redis.asyncio import Redis

from app.jobs.errors import BudgetExhaustedError
from app.metering.context import CallContext

#: One key per envelope. The scope is `CallContext.budget_key`.
SPEND_KEY = "spend:{scope}"

#: Long enough to outlive any job, short enough that abandoned counters do not accumulate.
SPEND_TTL_S = 7 * 24 * 3600

#: Work with no research job behind it — a parse, a health probe — is short-lived, and its
#: counter should not outlive it by a week.
UNJOBBED_SPEND_TTL_S = 300


class SpendLedger:
    """What one request has spent so far, shared by every caller working on it."""

    def __init__(self, redis: Redis, ctx: CallContext) -> None:
        self._redis = redis
        self._ctx = ctx
        self._key = SPEND_KEY.format(scope=ctx.budget_key) if ctx.budget_key else ""

    @property
    def enforced(self) -> bool:
        """False when nothing bounds this request: no cap set, or nothing to count against."""
        return bool(self._key) and self._ctx.cost_cap_micros > 0

    async def reserve(self, micros: int) -> None:
        """Claims `micros` before they are spent, or refuses the call.

        Claimed first and released after, rather than recorded afterwards, because two tasks
        running at once would otherwise both read a total under the cap and both spend past it.
        """
        if not self.enforced or micros <= 0:
            return
        total = await self._incr(micros)
        cap = self._ctx.cost_cap_micros
        if total > cap:
            await self._incr(-micros)
            raise BudgetExhaustedError(
                f"this would spend {total} of {cap} micros allowed for {self._ctx.budget_key}"
            )

    async def release(self, micros: int) -> None:
        """Gives back micros that were claimed but not spent — a refused or unbilled call."""
        if not self.enforced or micros <= 0:
            return
        await self._incr(-micros)

    async def total(self) -> int:
        if not self._key:
            return 0
        raw = await self._redis.get(self._key)
        return int(raw) if raw else 0

    async def _incr(self, micros: int) -> int:
        total = int(await self._redis.incrby(self._key, micros))
        ttl = SPEND_TTL_S if self._ctx.research_job_id else UNJOBBED_SPEND_TTL_S
        await self._redis.expire(self._key, ttl)
        return total
