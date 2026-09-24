"""`discovery.*` — running one planned search and keeping what it found (task 2.11, docs/06 §3).

This is the first thing in the system that turns money into leads, so the order matters as much
as the work:

1. check the cancel flag (ADR-0008) — the envelope may have waited in the stream while the user
   changed their mind, and the shared client checks again before every paid call;
2. claim the task, so a redelivery cannot run it twice;
3. search — the one paid step, bounded by the task's own cap;
4. write what came back, in one transaction;
5. charge for what was genuinely new, then report.

Between 3 and 4 the money is spent and the results exist only in memory. That is the one window
worth being careful about, which is why the write is a single transaction and why the credits are
charged from what the write actually returned rather than from what the search returned.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.connectors.registry import ConnectorRegistry
from app.connectors.types import DiscoveryQuery
from app.db.graph import GraphRepo
from app.db.reference import ReferenceData
from app.db.research_jobs import ResearchJobsRepo
from app.db.research_tasks import ResearchTasksRepo
from app.jobs.cancellation import JobCancelledError, raise_if_cancelled
from app.jobs.context import JobContext
from app.jobs.errors import ErrorClass, InvalidInputError, classify
from app.jobs.redact import redact
from app.jobs.registry import HandlerRegistry
from app.metering.context import CallContext
from app.metering.usage import UsageRecorder
from app.planner.plan import TASK_TYPE_BY_SOURCE

#: Credits per delivered lead, by the depth the user chose (`db/seeds/billing.ts`). Charged once
#: per business we did not already have: a job pays for what it found, not for what it looked at.
CREDIT_METER_BY_DEPTH: dict[str, str] = {
    "quick": "research_quick",
    "standard": "research_standard",
    "deep": "research_deep",
}
DEFAULT_DEPTH = "standard"


class DiscoveryPayload(BaseModel):
    """The planner's task input, re-validated here.

    `extra="forbid"` because this arrives as jsonb written by an older deploy of the planner: a
    field this version does not understand is worth failing on rather than silently ignoring.
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=64)
    query: "DiscoveryQueryPayload"
    area_slug: str = Field(min_length=1, max_length=128)
    locality: str | None = None
    depth: str | None = None


class DiscoveryQueryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=400)
    bbox: tuple[float, float, float, float] | None = None
    country: str | None = Field(default=None, max_length=2)
    category: str | None = None
    language: str = "en"
    max_results: int = Field(default=20, ge=1, le=200)


DiscoveryPayload.model_rebuild()


def register_discovery_handlers(
    registry: HandlerRegistry,
    *,
    connectors: ConnectorRegistry,
    reference: ReferenceData,
    graph: GraphRepo,
    tasks: ResearchTasksRepo,
    jobs: ResearchJobsRepo,
    usage: UsageRecorder,
) -> None:
    async def on_failure(
        envelope: Any, error_class: ErrorClass, error: str, attempts: int | None
    ) -> None:
        """Records the outcome the consumer decided, once it has stopped retrying.

        Without this a dead-lettered or paused task stays `running` for ever, and the job it
        belongs to can never finish because `finish_if_done` waits on it.
        """
        if envelope.org_id is None:
            return
        await tasks.mark_failed(
            str(envelope.org_id), str(envelope.job_id), error_class.value, cost_micros=0
        )

    for task_type in TASK_TYPE_BY_SOURCE.values():

        @registry.register(task_type, stage="discovery", on_failure=on_failure)
        async def run_discovery(ctx: JobContext) -> None:
            org_id = ctx.org_id
            if org_id is None:
                raise InvalidInputError("a discovery task needs an org context")
            research_job_id = _research_job_id(ctx)
            task_id = str(ctx.envelope.job_id)

            try:
                payload = DiscoveryPayload.model_validate(ctx.envelope.payload)
            except ValidationError as exc:
                raise InvalidInputError(
                    f"invalid discovery payload: {exc.error_count()} error(s)"
                ) from exc

            try:
                await raise_if_cancelled(ctx.redis, research_job_id)
                if not await tasks.mark_running(org_id, task_id, ctx.envelope.attempt):
                    # Already finished — a redelivery, or a message reclaimed after this task
                    # had completed. Running it again would pay for the same search twice.
                    ctx.log.info("discovery task already finished; nothing to do", task=task_id)
                    # Still ask whether the job is over. The attempt that finished this task may
                    # have died before it got to check, and if no later task ever checks either
                    # the job sits at `running` for ever with its credits reserved.
                    await jobs.finish_if_done(org_id, research_job_id)
                    return
                await jobs.mark_running(org_id, research_job_id)
                workspace_id = await _workspace(jobs, org_id, research_job_id)

                found = await _search(ctx, payload, connectors=connectors, org_id=org_id)
                stored = await graph.store(
                    org_id,
                    found,
                    source_id=await reference.source_id(payload.source),
                    workspace_id=workspace_id,
                    research_job_id=research_job_id,
                )
            except JobCancelledError:
                # The user stopped it and the API has already released the credits. Nothing
                # after this point may spend or charge (ADR-0008).
                await tasks.mark_cancelled(org_id, task_id)
                ctx.log.info("discovery task cancelled", task=task_id)
                return
            except Exception as exc:
                # Record the failure on the task before it propagates, so the consumer's own
                # retry decision does not depend on this having happened.
                await tasks.mark_failed(org_id, task_id, classify(exc).value, cost_micros=0)
                raise

            new_leads = sum(1 for s in stored if s.is_new)
            values = sum(s.values_written for s in stored)
            credits = await _charge(
                ctx,
                usage,
                org_id=org_id,
                research_job_id=research_job_id,
                depth=payload.depth or DEFAULT_DEPTH,
                new_leads=new_leads,
                reference=reference,
            )

            counts = {"candidates": len(found), "leads": new_leads, "values": values}
            await tasks.mark_completed(org_id, task_id, {**counts, "credits": credits}, 0)

            # Reporting must not undo a task that worked. Everything above this line is paid for
            # and stored; letting a counter update take it down would mark successful, charged
            # work as failed — which is exactly what a live Delhi run did to seventeen tasks.
            try:
                await jobs.add_progress(org_id, research_job_id, counts)
                await ctx.progress.publish(
                    job_id=research_job_id,
                    org_id=org_id,
                    trace_id=ctx.envelope.trace_id,
                    stage="discovery",
                    # Never "completed": one task finishing is not the job finishing, and the
                    # API's SSE stream closes on a terminal status (`progress-stream.ts`).
                    status="running",
                    counts=counts,
                    credits_used=credits,
                )
            except Exception as exc:
                ctx.log.warning(
                    "discovery task finished but its progress could not be reported",
                    task=task_id,
                    error_class=classify(exc).value,
                    error=redact(str(exc)),
                )
            await jobs.finish_if_done(org_id, research_job_id)
            ctx.log.info(
                "discovery task done",
                task=task_id,
                research_job_id=research_job_id,
                **counts,
                credits=credits,
            )


async def _workspace(jobs: ResearchJobsRepo, org_id: str, research_job_id: str) -> str:
    """The workspace a job's leads belong to.

    Read from the job row rather than the envelope, and required: a lead with no workspace is a
    lead nobody can see, and retrying cannot conjure one, so this fails fast rather than looking
    transient.
    """
    workspace_id = await jobs.workspace_of(org_id, research_job_id)
    if workspace_id is None:
        raise InvalidInputError(
            f"research job {research_job_id} has no workspace to deliver leads to"
        )
    return workspace_id


async def _search(
    ctx: JobContext,
    payload: DiscoveryPayload,
    *,
    connectors: ConnectorRegistry,
    org_id: str,
) -> list[Any]:
    """The one paid step, bounded by this task's own share of the job's budget."""
    connector = connectors.get(payload.source)
    query = DiscoveryQuery(
        text=payload.query.text,
        bbox=payload.query.bbox,
        category=payload.query.category,
        country=payload.query.country,
        language=payload.query.language,
        max_results=payload.query.max_results,
    )
    return await connector.search(query, CallContext.from_job(ctx))


async def _charge(
    ctx: JobContext,
    usage: UsageRecorder,
    *,
    org_id: str,
    research_job_id: str,
    depth: str,
    new_leads: int,
    reference: ReferenceData,
) -> int:
    """Bills the job for the businesses it actually found.

    Per lead delivered to this workspace, not per search and not per candidate: a job that
    rediscovers the same shop through five phrasings has delivered one lead and pays for one, and
    a company another customer found first is still a lead this one did not have (ADR-0012). The
    unit key is the task's own id, so a redelivered or retried task cannot charge twice.
    """
    if new_leads <= 0:
        return 0
    meter = CREDIT_METER_BY_DEPTH.get(depth, CREDIT_METER_BY_DEPTH[DEFAULT_DEPTH])
    credits = new_leads * await reference.credits_per_unit(meter)
    if credits <= 0:
        return 0
    recorded = await usage.record(
        org_id=org_id,
        research_job_id=research_job_id,
        meter=meter,
        unit_key=f"{meter}:{ctx.envelope.job_id}",
        cost_micros=0,
        units=new_leads,
        credits=credits,
    )
    return credits if recorded else 0


def _research_job_id(ctx: JobContext) -> str:
    envelope = ctx.envelope
    if envelope.research_job_id is None:
        raise InvalidInputError("a discovery task needs a research_job_id")
    return str(envelope.research_job_id)


__all__ = ["register_discovery_handlers"]
