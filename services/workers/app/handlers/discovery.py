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

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Final

from leadforge_contracts.job_envelope import JobEnvelope
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.connectors.registry import ConnectorRegistry
from app.connectors.types import DiscoveryQuery
from app.crawl.discovery import is_crawlable
from app.db.graph import GraphRepo
from app.db.reference import ReferenceData
from app.db.research_jobs import ResearchJobsRepo
from app.db.research_tasks import ResearchTasksRepo
from app.handlers.crawl import CRAWL_CREDIT_BUDGET
from app.handlers.crawl import JOB_TYPE as CRAWL_JOB_TYPE
from app.jobs.cancellation import JobCancelledError, raise_if_cancelled
from app.jobs.context import JobContext
from app.jobs.envelope import stream_for
from app.jobs.errors import RETRYABLE, ErrorClass, InvalidInputError, classify
from app.jobs.publisher import publish
from app.jobs.redact import redact
from app.jobs.registry import FailureHook, HandlerRegistry
from app.metering.context import CallContext
from app.metering.usage import UsageRecorder
from app.normalize.domains import is_platform_host
from app.planner.plan import TASK_TYPE_BY_SOURCE, PlannedTask

#: Credits per delivered lead, by the depth the user chose (`db/seeds/billing.ts`). Charged once
#: per business we did not already have: a job pays for what it found, not for what it looked at.
#: A delivered lead is billed by the source that found it, because that is what it cost us.
#: Since ADR-0017 the spread is two orders of magnitude: a lead out of the bulk place index
#: costs nothing, where a Google Places lead cost about Rs 2.21 measured over a real sweep.
CREDIT_METER_BY_SOURCE: str = "lead_{source}"

#: What a lead costs when its source has no rate of its own, by depth. A source added later
#: charges this until somebody prices it: free has to be a decision, never a default.
CREDIT_METER_BY_DEPTH: dict[str, str] = {
    "quick": "research_quick",
    "standard": "research_standard",
    "deep": "research_deep",
}
DEFAULT_DEPTH = "standard"

#: Must match the worker setting `CRAWL_POOL`, and a pool a worker consumes.
DEFAULT_CRAWL_POOL = "crawl"


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

    def with_max_results(self, max_results: int) -> "DiscoveryPayload":
        """The same task, asking for no more than the job still has room for.

        A copy rather than a mutation: the envelope is the record of what was planned, and a
        retry must re-read it, not whatever an earlier attempt narrowed it to.
        """
        return self.model_copy(
            update={"query": self.query.model_copy(update={"max_results": max_results})}
        )


class DiscoveryQueryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=400)
    bbox: tuple[float, float, float, float] | None = None
    country: str | None = Field(default=None, max_length=2)
    #: One category, for a source that takes one. Places accepts a single `includedType` per
    #: request, so this is the first of `categories`.
    category: str | None = None
    #: Every category the request means, for a source that can match a list in one query. The
    #: place index can, and an industry meaning three Overture categories would lose two thirds
    #: of its market if only the first were used (ADR-0017).
    categories: list[str] = Field(default_factory=list, max_length=64)
    #: The trade the user named, without the place. What a literal matcher searches names for
    #: when no category is known; `text` is a phrase and matches nothing as a name.
    name_hint: str | None = Field(default=None, max_length=100)
    #: `ResearchSpec.filters.has_website`, carried through so a source that can filter on it
    #: does, and so the delivery filter below can hold every source to it. Three states: True
    #: requires a website, False requires none, None does not care.
    has_website: bool | None = None
    #: `ResearchSpec.filters.contact.any_of` -- the channels the user will actually reach people
    #: on. A source filters on the ones it holds; `_honour_contact_filter` holds every source to
    #: them at delivery.
    contact_any_of: list[str] = Field(default_factory=list, max_length=8)
    language: str = "en"
    #: Matches the contract's own ceiling (`research-spec.schema.json`, Limits.max_results). It
    #: was 200 here while the schema allowed 10,000, so a spec the API accepted could be refused
    #: by the worker that had to run it.
    max_results: int = Field(default=20, ge=1, le=10_000)


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
    crawl_pool: str = DEFAULT_CRAWL_POOL,
) -> None:
    on_failure = _failure_hook(tasks, jobs)

    # Deduplicated, because two sources can share one task type: `overture` and `osm` are
    # different datasets with different provenance and different coverage, but both are read
    # out of the same place index by the same handler (ADR-0017). Registering per source would
    # register that handler twice and the registry refuses it.
    for task_type in dict.fromkeys(TASK_TYPE_BY_SOURCE.values()):

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

                # What the other searches have already delivered. Each search asks for the
                # job's whole target (`_results_per_task`), because they overlap too much to
                # divide it between them -- so this is where the job-wide ceiling is kept.
                #
                # Checked *before* the call, not after: a search this job no longer needs is a
                # paid API call with nothing to show for it, and on a free monthly allowance
                # that is the one kind of waste worth writing code to avoid.
                headroom = payload.query.max_results - await jobs.delivered_count(
                    org_id, research_job_id
                )
                if headroom <= 0:
                    await _skip_satisfied(
                        ctx,
                        tasks,
                        jobs,
                        org_id=org_id,
                        task_id=task_id,
                        research_job_id=research_job_id,
                        target=payload.query.max_results,
                    )
                    return
                found = await _search(
                    ctx,
                    payload.with_max_results(headroom),
                    connectors=connectors,
                    org_id=org_id,
                )
                found = _honour_website_filter(found, payload.query.has_website)
                found = _honour_contact_filter(found, payload.query.contact_any_of)
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
                # A task that will be retried stays `running`, and only a task that is
                # finished for good is written as `failed`.
                #
                # Both halves matter. `finish_if_done` completes a job once nothing of it is
                # queued or running, so writing `failed` here let the job finish while a retry
                # was still scheduled -- the retry would then deliver leads to a job the user had
                # already been told was done, and spend credits the settlement had closed the
                # books on. And the retry could not have run anyway: claiming the row excludes
                # tasks that have stopped.
                #
                # The consumer records the real failure through its `on_failure` hook once it
                # stops retrying, including on dead-letter, so nothing goes unrecorded and no
                # task is left running for ever.
                error_class = classify(exc)
                if error_class not in RETRYABLE:
                    await tasks.mark_failed(org_id, task_id, error_class.value, cost_micros=0)
                    # And ask whether that was the last of them. `finish_if_done` otherwise
                    # only runs at the end of a task that succeeded, so a job whose final task
                    # fails stays `running` for ever with its credits reserved -- the
                    # customer's credits, for leads they never received.
                    await jobs.finish_if_done(org_id, research_job_id)
                raise

            # Enqueued before this task reports itself finished, and that order is the whole
            # trick: while this task is still `running`, `finish_if_done` cannot complete the job,
            # so the crawl rows it adds are guaranteed to be waited for. Saving them after
            # `mark_completed` would let a job finish between the two and the emails arrive for a
            # job already reported done.
            await _enqueue_crawls(
                ctx,
                found,
                stored,
                org_id=org_id,
                research_job_id=research_job_id,
                depth=payload.depth or DEFAULT_DEPTH,
                country=payload.query.country,
                tasks=tasks,
                pool=crawl_pool,
            )

            new_leads = sum(1 for s in stored if s.is_new)
            # Two different numbers, and the customer is owed both (ADR-0015). `delivered` is
            # every lead this search put in front of them; `new_leads` is the subset they had not
            # been given before, which is the only one billing may count. A second run of the
            # same search delivers everything and charges for nothing.
            delivered = sum(1 for s in stored if s.lead_id is not None)
            values = sum(s.values_written for s in stored)
            credits = await _charge(
                ctx,
                usage,
                org_id=org_id,
                research_job_id=research_job_id,
                depth=payload.depth or DEFAULT_DEPTH,
                source=payload.source,
                new_leads=new_leads,
                reference=reference,
            )

            counts = {
                "candidates": len(found),
                "leads": delivered,
                "new_leads": new_leads,
                "values": values,
            }
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


async def _skip_satisfied(
    ctx: JobContext,
    tasks: ResearchTasksRepo,
    jobs: ResearchJobsRepo,
    *,
    org_id: str,
    task_id: str,
    research_job_id: str,
    target: int,
) -> None:
    """Closes a search the job no longer needs, without making a call.

    Completed rather than cancelled or failed: there was nothing left to do, which is a success.
    It still asks whether the job is over, because this may well be the last task of it and
    nothing else would ever check.
    """
    ctx.log.info(
        "discovery task skipped; the job already has what it asked for",
        task=task_id,
        research_job_id=research_job_id,
        target=target,
    )
    await tasks.mark_completed(
        org_id,
        task_id,
        {"candidates": 0, "leads": 0, "values": 0, "skipped": "target reached"},
        0,
    )
    await jobs.finish_if_done(org_id, research_job_id)


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
        # Per-connector extras. Dropped here once, which meant the place index only ever saw
        # the first of the categories the planner had worked out for it.
        options=_options(payload),
    )
    return await connector.search(query, CallContext.from_job(ctx))


#: Contact channels a candidate can be judged on at delivery. `whatsapp` and `contact_form` are
#: in the spec and not in a discovery result, so they are deliberately absent.
_CHECKABLE_CONTACT: Final[frozenset[str]] = frozenset({"phone", "email"})


def _honour_website_filter(found: list[Any], has_website: bool | None) -> list[Any]:
    """Holds every source to `has_website`, including the ones that cannot filter on it.

    The place index filters in SQL, which is what keeps the job's result cap from being spent
    on rows that will be thrown away. Google Places has no such parameter, so without this a
    search for "restaurants in Delhi with a website" delivers whatever it finds.

    It did. The spec parsed the filter correctly and nothing in the pipeline read it: a live run
    delivered 225 restaurants of which 50 had a website -- the user asked for one thing and was
    charged for another. The filter is cheap and the guarantee belongs at the last point before
    storage, where it covers sources added later too.
    """
    if has_website is None:
        return found
    return [c for c in found if bool(getattr(c, "url", None)) is has_website]


def _honour_contact_filter(found: list[Any], any_of: Sequence[str]) -> list[Any]:
    """Keeps only results reachable on one of the channels the user named.

    `filters.contact.any_of` is the closest thing the spec has to the product's actual promise:
    a lead nobody can be reached at is not a lead. It was parsed, stored, and read by nothing --
    the same gap as `has_website`, found by auditing every filter in the schema at once rather
    than waiting for another live run to expose one.

    Only `phone` and `email` can be judged here. `whatsapp` and `contact_form` are channels the
    spec allows and discovery cannot see, so a request naming only those narrows nothing, and
    `ai/feasibility.py` tells the user that instead of this quietly keeping everything or
    quietly dropping everything.
    """
    channels = [c for c in any_of if c in _CHECKABLE_CONTACT]
    if not channels:
        return found
    return [c for c in found if any(_has_channel(c, channel) for channel in channels)]


def _has_channel(candidate: Any, channel: str) -> bool:
    """Whether a candidate carries one contact channel.

    Both places have to be looked in, and finding that out is what the test for this caught:
    `Candidate` has a `phone` attribute and no `email` one -- an email arrives as a `FieldValue`
    in `values`, like most of what a source reports. Reading attributes alone made the email
    channel match nothing, which would have delivered an empty result for "restaurants with an
    email" and looked like a thin market.
    """
    if getattr(candidate, channel, None):
        return True
    return any(
        getattr(value, "field", None) == channel and getattr(value, "value", None)
        for value in getattr(candidate, "values", ())
    )


def _options(payload: DiscoveryPayload) -> dict[str, Any]:
    """Per-connector extras. A source that does not understand one ignores it."""
    options: dict[str, Any] = {}
    if payload.query.categories:
        options["categories"] = payload.query.categories
    if payload.query.name_hint:
        options["name_hint"] = payload.query.name_hint
    if payload.query.has_website is not None:
        options["has_website"] = payload.query.has_website
    if payload.query.contact_any_of:
        options["contact_any_of"] = payload.query.contact_any_of
    return options


async def _charge(
    ctx: JobContext,
    usage: UsageRecorder,
    *,
    org_id: str,
    research_job_id: str,
    depth: str,
    source: str,
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
    meter, rate = await _lead_rate(reference, source=source, depth=depth)
    credits = new_leads * rate
    if credits <= 0:
        # A free source. Nothing to record, and the job's reservation is released at settlement
        # (ADR-0014), so the user is charged for what the leads cost rather than what was held.
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


async def _lead_rate(reference: ReferenceData, *, source: str, depth: str) -> tuple[str, int]:
    """What one delivered lead costs, and the meter it is billed under.

    The source's own rate where it has one. A source with no rate falls back to the depth meter
    rather than to zero: an unpriced source is one nobody has thought about yet, and a bug that
    gives work away is still a bug.
    """
    by_source = CREDIT_METER_BY_SOURCE.format(source=source)
    rate = await reference.credit_rate(by_source)
    if rate is not None:
        return by_source, rate
    by_depth = CREDIT_METER_BY_DEPTH.get(depth, CREDIT_METER_BY_DEPTH[DEFAULT_DEPTH])
    return by_depth, await reference.credits_per_unit(by_depth)


def _failure_hook(tasks: ResearchTasksRepo, jobs: ResearchJobsRepo) -> FailureHook:
    """What to do once the consumer has stopped retrying a discovery task."""

    async def on_failure(
        envelope: Any, error_class: ErrorClass, error: str, attempts: int | None
    ) -> None:
        """Records the outcome the consumer decided, once it has stopped retrying.

        Without this a dead-lettered or paused task stays `running` for ever, and the job it
        belongs to can never finish because `finish_if_done` waits on it.

        And then something has to ask. Marking the task was only half of it: `finish_if_done`
        runs at the end of a *successful* task, so a job whose last task failed stayed `running`
        with its credits reserved and nothing left to trigger the check. Two such jobs sat on
        this database for two hours holding 500 credits between them, one of them with every
        single task failed.
        """
        if envelope.org_id is None:
            return
        org_id = str(envelope.org_id)
        await tasks.mark_failed(org_id, str(envelope.job_id), error_class.value, cost_micros=0)
        if envelope.research_job_id is not None:
            # Settlement releases what the job did not spend (ADR-0014), so a job that ends in
            # failure has to end rather than linger: the credits are the customer's.
            await jobs.finish_if_done(org_id, str(envelope.research_job_id))

    return on_failure


def _research_job_id(ctx: JobContext) -> str:
    envelope = ctx.envelope
    if envelope.research_job_id is None:
        raise InvalidInputError("a discovery task needs a research_job_id")
    return str(envelope.research_job_id)


async def _enqueue_crawls(
    ctx: JobContext,
    found: list[Any],
    stored: list[Any],
    *,
    org_id: str,
    research_job_id: str,
    depth: str,
    country: str | None,
    tasks: ResearchTasksRepo,
    pool: str,
) -> int:
    """Queues one crawl per business whose own website we can read. Returns how many.

    Only where there is a site worth reading. A business with no website has nothing for us to
    crawl, and one reachable only through `business.site` or a Facebook page has a platform's
    address rather than its own -- crawling that would read the platform's markup and attribute it
    to this company.

    Failing to enqueue must not fail the search. The leads are already stored and charged for; a
    crawl that never starts costs the user some emails, where raising here would throw away a
    search that worked.
    """
    planned: list[PlannedTask] = []
    for candidate, record in zip(found, stored, strict=False):
        website = getattr(candidate, "url", None)
        if not website or is_platform_host(website) or not is_crawlable(website, website):
            continue
        planned.append(
            PlannedTask(
                type=CRAWL_JOB_TYPE,
                input={
                    "company_id": record.company_id,
                    "website": website,
                    "depth": depth,
                    "country": country,
                },
                credit_budget=CRAWL_CREDIT_BUDGET,
            )
        )
    if not planned:
        return 0

    try:
        saved = await tasks.save_plan(org_id, research_job_id, planned)
        stream = stream_for(pool)
        now = datetime.now(UTC).isoformat()
        for entry in saved:
            if not entry.inserted:
                # A redelivery of this search: the crawl was queued the first time round, and its
                # own idempotency guard would catch it anyway. Not publishing is cheaper.
                continue
            await publish(
                ctx.redis,
                stream,
                JobEnvelope.model_validate(
                    {
                        "envelope_version": 1,
                        "job_id": entry.id,
                        "research_job_id": research_job_id,
                        "org_id": org_id,
                        "type": CRAWL_JOB_TYPE,
                        "trace_id": ctx.envelope.trace_id,
                        "attempt": 1,
                        "priority": ctx.envelope.priority,
                        "idempotency_key": f"{CRAWL_JOB_TYPE}:{entry.id}",
                        # Nothing to spend: a crawl calls no provider.
                        "budget": {"credits_remaining": 0, "cost_cap_micros": 0},
                        "payload": entry.task.input,
                        "created_at": now,
                    }
                ),
            )
    except Exception as exc:
        ctx.log.warning(
            "leads were stored but their crawls could not be queued",
            research_job_id=research_job_id,
            error_class=classify(exc).value,
            error=redact(str(exc)),
        )
        return 0
    return len(planned)


__all__ = ["register_discovery_handlers"]
