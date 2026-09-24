"""`research.plan` — the handler that turns a spec into queued work (task 2.10, docs/06 §2).

It is the first thing that runs after a user presses go, and the only thing between their
sentence and money being spent, so the order below is deliberate:

1. check the cancel flag (ADR-0008) — before anything, because the envelope may have sat in the
   stream while the user changed their mind;
2. expand the query with the one model call this stage makes;
3. build the plan, which is pure and decides every budget;
4. write it in one transaction, idempotently;
5. check the flag again, then fan out.

Between (4) and (5) the plan exists but nothing has been queued. That is the cheapest possible
place to be interrupted, and it is where the second check goes.
"""

from datetime import UTC, datetime
from typing import Any

from leadforge_contracts.job_envelope import Budget, JobEnvelope
from leadforge_contracts.research_spec import ResearchSpec
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.gateway import AiGateway
from app.connectors.registry import ConnectorRegistry
from app.db.reference import ReferenceData
from app.db.research_jobs import ResearchJobsRepo
from app.db.research_tasks import ResearchTasksRepo, SavedTask
from app.jobs.cancellation import JobCancelledError, raise_if_cancelled
from app.jobs.context import JobContext
from app.jobs.envelope import stream_for
from app.jobs.errors import (
    BudgetExhaustedError,
    InvalidInputError,
    classify,
)
from app.jobs.publisher import publish
from app.jobs.redact import redact
from app.jobs.registry import HandlerRegistry
from app.metering.context import CallContext
from app.planner.capability import build_capabilities
from app.planner.plan import (
    DEFAULT_MICROS_PER_CREDIT,
    Expansion,
    Plan,
    build_plan,
    text_of,
)
from app.planner.templates import template_for

#: Where discovery envelopes go. Defaults to the pool the plan itself arrived on, so a worker
#: configured for one stream runs both halves. Hardcoding "discovery" would repeat the mistake
#: `_micros_for` exists to avoid: the API publishes to `JOBS_DISCOVERY_POOL`, which is
#: configurable and which its own comment says tests override per run — the plan would arrive on
#: the configured stream and the fan-out would land on one nobody consumes.
DEFAULT_DISCOVERY_POOL = "discovery"

#: Clamped rather than trusted: these come from a model, and the schema deliberately does not
#: bound array lengths (strict structured output is only verified to accept `minimum`/`maximum`).
#: An expansion of 200 phrases would otherwise become 200 paid searches.
MAX_QUERIES = 8
MAX_SUB_LOCALITIES = 20


class PlanPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_id: str = Field(min_length=1, max_length=64)
    spec: dict[str, Any]


def register_planner_handlers(
    registry: HandlerRegistry,
    *,
    gateway: AiGateway | None,
    connectors: ConnectorRegistry,
    reference: ReferenceData,
    tasks: ResearchTasksRepo,
    jobs: ResearchJobsRepo,
    pool: str = DEFAULT_DISCOVERY_POOL,
) -> None:
    """Registers `research.plan`.

    `gateway` may be None. Expansion is the one model call this stage makes and it already
    degrades to planning the request as written, so a worker with no model key can still plan —
    refusing to register would leave `research.plan` with no handler and the job hanging, for a
    stage that does not need a model.
    """

    @registry.register("research.plan", stage="planning")
    async def plan_research(ctx: JobContext) -> None:
        org_id = ctx.org_id
        if org_id is None:
            raise InvalidInputError("research.plan needs an org context")
        try:
            payload = PlanPayload.model_validate(ctx.envelope.payload)
            spec = ResearchSpec.model_validate(payload.spec)
        except ValidationError as exc:
            # A spec that does not validate cannot be planned, and will not validate on a retry.
            raise InvalidInputError(f"invalid plan payload: {exc.error_count()} error(s)") from exc

        research_job_id = _research_job_id(ctx)
        try:
            await raise_if_cancelled(ctx.redis, research_job_id)
            plan = await _plan(
                ctx,
                spec,
                gateway=gateway,
                connectors=connectors,
                ref=reference,
                credits=ctx.envelope.budget.credits_remaining,
                micros_per_credit=_micros_for(1, ctx.envelope.budget),
            )
            # Checked before the write, not after: a plan written for a job the user has already
            # stopped is a set of rows nobody will ever run, and it left them `queued` for ever.
            await raise_if_cancelled(ctx.redis, research_job_id)
            saved = await tasks.save_plan(org_id, research_job_id, plan.tasks)
            await raise_if_cancelled(ctx.redis, research_job_id)
            await _fan_out(ctx, saved, org_id=org_id, research_job_id=research_job_id, pool=pool)
        except JobCancelledError:
            # Not a failure: the user stopped it and the API has already released the credits.
            ctx.log.info("plan abandoned; the job was cancelled", research_job_id=research_job_id)
            await jobs.mark_cancelled(org_id, research_job_id)
            await _publish(ctx, org_id, research_job_id, status="cancelled")
            return

        if not plan.tasks:
            # Nothing to run, so nothing downstream will ever finish this job. Left alone it
            # sits at `planning` for ever with the user's credits still reserved and no visible
            # failure — a silent failure of exactly the kind CLAUDE.md forbids.
            reason = _summary(plan) or "this request produced no searches"
            await jobs.mark_failed(org_id, research_job_id, _error_class(plan), reason)
            await _publish(
                ctx,
                org_id,
                research_job_id,
                status="failed",
                message=reason,
                error_class=_error_class(plan),
            )
            ctx.log.warning(
                "nothing to plan",
                research_job_id=research_job_id,
                reason=reason,
                unsatisfiable=list(plan.unsatisfiable),
                unknown_places=list(plan.unknown_places),
            )
            return

        await _publish(
            ctx,
            org_id,
            research_job_id,
            status="completed",
            counts={"tasks": len(saved)},
            message=_summary(plan),
        )
        ctx.log.info(
            "planned",
            research_job_id=research_job_id,
            tasks=len(saved),
            # A redelivered envelope re-reads its plan instead of buying it again; this is how
            # that shows up in the log rather than looking like a fresh plan.
            newly_inserted=sum(1 for entry in saved if entry.inserted),
            credits=plan.total_credits,
            reserved=ctx.envelope.budget.credits_remaining,
            unsatisfiable=list(plan.unsatisfiable),
            unknown_places=list(plan.unknown_places),
        )


async def _plan(
    ctx: JobContext,
    spec: ResearchSpec,
    *,
    gateway: AiGateway | None,
    connectors: ConnectorRegistry,
    ref: ReferenceData,
    credits: int,
    micros_per_credit: int,
) -> Plan:
    capabilities = build_capabilities(connectors, [str(f) for f in spec.fields])
    expansion = await _expand(ctx, spec, gateway)
    return await build_plan(
        spec,
        capabilities=capabilities,
        template=template_for(str(spec.intent)),
        reference=ref,
        expansion=expansion,
        credits=credits,
        micros_per_credit=micros_per_credit or DEFAULT_MICROS_PER_CREDIT,
    )


async def _expand(ctx: JobContext, spec: ResearchSpec, gateway: AiGateway | None) -> Expansion:
    """The one model call this stage makes (docs/06: the LLM only expands queries).

    Falls back to the literal request if the model is unavailable. A plan of one plain search is
    a worse plan, not a broken one, and refusing to plan at all would cost the user their whole
    job because a synonym list could not be fetched.
    """
    request = _request_text(spec)
    if gateway is None:
        return Expansion(queries=(request,))
    country = spec.filters.location.country if spec.filters.location else None
    try:
        result = await gateway.run(
            "query_expand",
            {"query": request, "country": country},
            CallContext.from_job(ctx),
        )
    except BudgetExhaustedError:
        # Not something to shrug off into a one-query plan: the job has no money for the model,
        # and it has none for the searches either. Let it surface as budget_exhausted.
        raise
    except Exception as exc:
        # Classified and redacted like every other failure: the raw exception from an HTTP client
        # can carry a URL with the key in its query string (app/jobs/redact.py exists for that).
        ctx.log.warning(
            "query expansion unavailable; planning the request as written",
            error_class=classify(exc).value,
            error=redact(str(exc)),
        )
        return Expansion(queries=(request,))

    data = result.data
    queries = [str(q).strip() for q in data.get("queries", []) if str(q).strip()]
    localities = [str(s).strip() for s in data.get("sub_localities", []) if str(s).strip()]
    if not queries:
        queries = [request]
    return Expansion(
        queries=tuple(dict.fromkeys(queries))[:MAX_QUERIES],
        sub_localities=tuple(dict.fromkeys(localities))[:MAX_SUB_LOCALITIES],
    )


def _request_text(spec: ResearchSpec) -> str:
    """The user's request rebuilt from the spec, for the expander to widen.

    The raw sentence is not on the envelope — the API sends the reviewed spec, which is the
    thing the user actually approved — so this reassembles the searchable part of it.
    """
    industry = spec.filters.industry
    include = industry.include if industry and industry.include else []
    terms = [text_of(t) for t in include] or ["businesses"]
    location = spec.filters.location
    places = (
        [text_of(p) for p in ((location.cities or []) + (location.states or []))]
        if location
        else []
    )
    where = places[0] if places else (location.country or "" if location else "")
    return f"{terms[0]} in {where}".strip() if where else terms[0]


async def _fan_out(
    ctx: JobContext, saved: list[SavedTask], *, org_id: str, research_job_id: str, pool: str
) -> None:
    """One envelope per task, each carrying only its own budget.

    `cost_cap_micros` is per request and callers must not decrement it (metering/context.py), so
    a task's envelope gets the task's own share rather than the job's total — otherwise every
    task would believe it could spend the whole job.
    """
    stream = stream_for(pool)
    now = datetime.now(UTC).isoformat()
    for entry in saved:
        envelope = JobEnvelope.model_validate(
            {
                "envelope_version": 1,
                "job_id": entry.id,
                "research_job_id": research_job_id,
                "org_id": org_id,
                "type": entry.task.type,
                "trace_id": ctx.envelope.trace_id,
                "attempt": 1,
                "priority": ctx.envelope.priority,
                # The task's own id: a redelivered plan fans out the same ids, so the executor's
                # guard recognises the repeat instead of running everything twice.
                "idempotency_key": f"{entry.task.type}:{entry.id}",
                "budget": {
                    "credits_remaining": entry.task.credit_budget,
                    "cost_cap_micros": _micros_for(entry.task.credit_budget, ctx.envelope.budget),
                },
                "payload": entry.task.input,
                "created_at": now,
            }
        )
        await publish(ctx.redis, stream, envelope)


def _micros_for(credits: int, budget: Budget) -> int:
    """A task's share of the job's internal cost cap, at the job's own rate.

    Derived from this envelope rather than from a constant. The API computes the job's cap as
    `credits * COST_CAP_MICROS_PER_CREDIT`, which is configurable (apps/api env.schema.ts); a
    second copy of that number here would agree until someone changed the setting, and then
    every task would quietly carry the wrong ceiling.

    Zero means uncapped and stays zero, so an uncapped job does not become 1000 capped tasks.
    """
    if budget.cost_cap_micros <= 0 or budget.credits_remaining <= 0:
        return 0
    return round(credits * budget.cost_cap_micros / budget.credits_remaining)


def _research_job_id(ctx: JobContext) -> str:
    """The research job this envelope plans for.

    The API sets `job_id == research_job_id` for a plan, but the contract allows them to differ,
    and the cancel flag is keyed on the research job. Reading the wrong one would check a flag
    nobody ever sets.
    """
    envelope = ctx.envelope
    if envelope.research_job_id is None:
        raise InvalidInputError("research.plan needs a research_job_id")
    return str(envelope.research_job_id)


async def _publish(
    ctx: JobContext,
    org_id: str,
    research_job_id: str,
    *,
    status: Any,
    counts: dict[str, int] | None = None,
    message: str | None = None,
    error_class: Any = None,
) -> None:
    await ctx.progress.publish(
        job_id=research_job_id,
        org_id=org_id,
        trace_id=ctx.envelope.trace_id,
        stage="planning",
        status=status,
        counts=counts or {},
        message=message,
        error_class=error_class,
    )


def _error_class(plan: Plan) -> str:
    """Why an empty plan is empty, in the taxonomy the rest of the system uses (docs/01).

    The distinction is the user's to act on: a request we cannot serve is `invalid_input` and
    wants rewording, where a budget too small for even one search is `budget_exhausted` and
    wants more credits.
    """
    if plan.budget_limited:
        return "budget_exhausted"
    return "invalid_input"


def _summary(plan: Plan) -> str | None:
    parts: list[str] = []
    if plan.unknown_places:
        parts.append(f"not in our geography seed: {', '.join(plan.unknown_places)}")
    if plan.unsatisfiable:
        parts.append(f"no source can fill: {', '.join(plan.unsatisfiable)}")
    parts.extend(plan.notes)
    return "; ".join(parts) or None


__all__ = ["DEFAULT_DISCOVERY_POOL", "register_planner_handlers"]
