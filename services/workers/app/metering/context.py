"""Who an outbound call is billed to. Shared by the connectors and the AI gateway."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from app.jobs.errors import InvalidInputError

if TYPE_CHECKING:  # keeps this module free of Redis and the progress publisher
    from app.jobs.context import JobContext


@dataclass(frozen=True, slots=True)
class CallContext:
    """What one outbound call is attributed to (docs/08, docs/07).

    Deliberately narrower than `JobContext`: a connector or a model call gets attribution and a
    logger, not the Redis handle or the progress publisher, so it cannot reach past its own
    responsibility. Everything we pay for carries one of these.
    """

    org_id: str
    research_job_id: str | None
    trace_id: str
    log: structlog.stdlib.BoundLogger
    #: Ceiling on what this whole request may spend internally, in micros (0 = uncapped).
    #: It is a total, not a remainder: callers must not decrement it as spending happens.
    cost_cap_micros: int = 0
    #: What the cap accumulates against: this envelope's own id. Retries of it share one counter,
    #: which is right — the first attempt's spend really did happen.
    spend_id: str = ""

    def __post_init__(self) -> None:
        if self.cost_cap_micros > 0 and not self.budget_key:
            # Otherwise the cap silently does nothing, which is how a parse came to be
            # uncapped in the first place.
            raise InvalidInputError("a capped call needs something to accumulate against")

    @property
    def budget_key(self) -> str:
        """The scope the running spend total is kept under.

        The envelope's own id, not the research job's. These were the same thing until the
        planner began fanning a job out into tasks with a share of its budget each: the cap now
        on a `CallContext` belongs to one task, so counting a whole job against it would cross it
        almost immediately and fail every task after the first as `budget_exhausted`. The key and
        the cap have to describe the same thing. The job-wide bound comes from the planner
        dividing the reservation, so the caps cannot add up to more than was reserved.
        """
        return self.spend_id or self.research_job_id or ""

    @classmethod
    def from_job(cls, ctx: "JobContext") -> "CallContext":
        if ctx.org_id is None:
            # Retrying cannot conjure an org, so this fails fast instead of looking transient.
            raise InvalidInputError("a metered call needs an org context; this envelope has none")
        envelope = ctx.envelope
        job_id = None if envelope.research_job_id is None else str(envelope.research_job_id)
        return cls(
            org_id=ctx.org_id,
            research_job_id=job_id,
            trace_id=envelope.trace_id,
            log=ctx.log,
            cost_cap_micros=envelope.budget.cost_cap_micros,
            spend_id=str(envelope.job_id),
        )
