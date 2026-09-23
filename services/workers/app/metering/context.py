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
    #: Ceiling on what this whole job may spend internally, in micros (0 = uncapped).
    #: It is a total, not a remainder: callers must not decrement it as spending happens.
    cost_cap_micros: int = 0

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
        )
