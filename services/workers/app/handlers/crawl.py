"""`crawl.company_site` — reading one lead's own website (docs/06 section 4, Phase 3).

This is the handler the rest of Phase 3 was built for, and the first thing in the product that
produces an email address. A maps listing never carries one; a contact page usually does.

**It costs nothing.** No provider is called, no spend is reserved and no credit is charged. The
business was found by a search and paid for then; this is its own website telling us more about
it. That matters beyond tidiness: it means a crawl can run as often as it likes on an account with
no budget at all.

**Being turned away is an outcome, not a failure.** A site behind Cloudflare, a contact page behind
a login, a `robots.txt` that disallows the path -- each of those ends that page and is counted, and
the task still completes. Failing the task would retry a refusal three times and then mark a job
broken because a site did what it is entitled to do.

**The frontier is used for its clock, not its queue.** `choose_pages` has already decided which
pages this company gets, so there is no queue to draw from; what is still needed is the promise
that no other worker is fetching this host at the same moment, which `take_host_slot` gives.

Order, and the reason for it:

1. check the cancel flag (ADR-0008) -- the envelope may have waited while the user changed their
   mind, and a crawl that costs nothing still costs the user's time and the site's bandwidth;
2. claim the task, so a redelivery does not crawl the same site twice;
3. fetch the homepage, and choose the rest of the pages from what it links to;
4. read each page, merging as we go, best evidence winning;
5. write the values in one transaction, then report.
"""

from typing import Any
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.connectors.types import FieldValue
from app.crawl.discovery import (
    PageRole,
    choose_pages,
    links_from,
    page_budget,
    sitemap_urls,
)
from app.crawl.fetcher import CrawlFetcher, Fetched
from app.crawl.frontier import Frontier, host_of
from app.crawl.mapping import SOURCE_KEY, merge_pages, values_from_page
from app.db.graph import GraphRepo
from app.db.reference import ReferenceData
from app.db.research_jobs import ResearchJobsRepo
from app.db.research_tasks import ResearchTasksRepo
from app.jobs.cancellation import JobCancelledError, raise_if_cancelled
from app.jobs.context import JobContext
from app.jobs.errors import (
    AccessRestrictedError,
    ErrorClass,
    InvalidInputError,
    classify,
)
from app.jobs.redact import redact
from app.jobs.registry import HandlerRegistry

JOB_TYPE = "crawl.company_site"


class CrawlPayload(BaseModel):
    """What a crawl task is told. `extra="forbid"`: an unknown field means a newer planner."""

    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(min_length=1, max_length=64)
    #: The company's website, as discovery recorded it.
    website: str = Field(min_length=4, max_length=2048)
    depth: str | None = None
    #: ISO-3166 alpha-2. Decides whether `98765 43210` reads as a phone number at all.
    country: str | None = Field(default=None, max_length=2)


def register_crawl_handlers(
    registry: HandlerRegistry,
    *,
    fetcher: CrawlFetcher,
    frontier: Frontier,
    graph: GraphRepo,
    reference: ReferenceData,
    tasks: ResearchTasksRepo,
    jobs: ResearchJobsRepo,
) -> None:
    async def on_failure(
        envelope: Any, error_class: ErrorClass, error: str, attempts: int | None
    ) -> None:
        """Records the outcome once the consumer has stopped retrying.

        Without it a dead-lettered task stays `running` and the job waiting on it never finishes.
        """
        if envelope.org_id is None:
            return
        await tasks.mark_failed(
            str(envelope.org_id), str(envelope.job_id), error_class.value, cost_micros=0
        )

    @registry.register(JOB_TYPE, stage="crawling", on_failure=on_failure)
    async def crawl_company_site(ctx: JobContext) -> None:
        org_id = ctx.org_id
        if org_id is None:
            raise InvalidInputError("a crawl task needs an org context")
        research_job_id = _research_job_id(ctx)
        task_id = str(ctx.envelope.job_id)

        try:
            payload = CrawlPayload.model_validate(ctx.envelope.payload)
        except ValidationError as exc:
            raise InvalidInputError(f"invalid crawl payload: {exc.error_count()} error(s)") from exc

        try:
            await raise_if_cancelled(ctx.redis, research_job_id)
            if not await tasks.mark_running(org_id, task_id, ctx.envelope.attempt):
                ctx.log.info("crawl task already finished; nothing to do", task=task_id)
                await jobs.finish_if_done(org_id, research_job_id)
                return

            outcome = await _crawl_site(
                ctx,
                payload,
                fetcher=fetcher,
                frontier=frontier,
                research_job_id=research_job_id,
            )
            written = await graph.add_company_values(
                org_id,
                payload.company_id,
                outcome.values,
                source_id=await reference.source_id(SOURCE_KEY),
            )
        except JobCancelledError:
            await tasks.mark_cancelled(org_id, task_id)
            ctx.log.info("crawl task cancelled", task=task_id)
            return
        except Exception as exc:
            await tasks.mark_failed(org_id, task_id, classify(exc).value, cost_micros=0)
            raise

        counts = {"values": written}
        await tasks.mark_completed(
            org_id,
            task_id,
            {
                **counts,
                "pages_read": outcome.pages_read,
                "pages_restricted": outcome.pages_restricted,
                "emails_seen": outcome.emails_seen,
                "fields": sorted({v.field for v in outcome.values}),
            },
            0,
        )

        # Reporting must not undo work that is already written, which a live Delhi run taught the
        # discovery handler the hard way: seventeen successful tasks were marked failed by a
        # counter update that threw.
        try:
            await jobs.add_progress(org_id, research_job_id, counts)
            await ctx.progress.publish(
                job_id=research_job_id,
                org_id=org_id,
                trace_id=ctx.envelope.trace_id,
                stage="crawling",
                status="running",
                counts=counts,
            )
        except Exception as exc:
            ctx.log.warning(
                "crawl finished but its progress could not be reported",
                task=task_id,
                error_class=classify(exc).value,
                error=redact(str(exc)),
            )
        await jobs.finish_if_done(org_id, research_job_id)
        ctx.log.info(
            "crawl task done",
            task=task_id,
            company_id=payload.company_id,
            pages_read=outcome.pages_read,
            pages_restricted=outcome.pages_restricted,
            values=written,
        )


class _Outcome:
    """What a whole site yielded. A plain class because it is built up as pages are read."""

    __slots__ = ("emails_seen", "pages_read", "pages_restricted", "values")

    def __init__(self) -> None:
        self.values: list[FieldValue] = []
        self.pages_read = 0
        self.pages_restricted = 0
        self.emails_seen = 0


async def _crawl_site(
    ctx: JobContext,
    payload: CrawlPayload,
    *,
    fetcher: CrawlFetcher,
    frontier: Frontier,
    research_job_id: str,
) -> _Outcome:
    """Reads the homepage, picks the rest from it, and merges what every page said."""
    outcome = _Outcome()
    homepage = await _read(ctx, payload.website, fetcher=fetcher, frontier=frontier)
    if homepage is None:
        outcome.pages_restricted += 1
        return outcome

    pages = [homepage]
    outcome.pages_read += 1
    budget = page_budget(payload.depth or "standard")
    chosen = choose_pages(
        homepage.url,
        links_from(homepage.body, homepage.url),
        sitemap=await _sitemap(ctx, homepage.url, fetcher=fetcher, frontier=frontier),
        limit=budget,
    )

    for candidate in chosen:
        if candidate.role is PageRole.HOMEPAGE:
            continue
        await raise_if_cancelled(ctx.redis, research_job_id)
        page = await _read(ctx, candidate.url, fetcher=fetcher, frontier=frontier)
        if page is None:
            outcome.pages_restricted += 1
            continue
        pages.append(page)
        outcome.pages_read += 1

    facts = [
        values_from_page(
            page.body,
            page_url=page.url,
            region=payload.country,
        )
        for page in pages
        if page.body
    ]
    outcome.values = merge_pages(facts)
    outcome.emails_seen = sum(f.emails_found for f in facts)
    return outcome


async def _read(
    ctx: JobContext, url: str, *, fetcher: CrawlFetcher, frontier: Frontier
) -> Fetched | None:
    """One page, or None when we were refused, told not to, or could not safely ask.

    None rather than an exception, because none of those is a failure of this job. A site is
    entitled to turn us away, and retrying a refusal three times would be both rude and pointless.
    """
    host = host_of(url)
    if host and not await frontier.wait_for_host(host):
        # Another worker is on this host and still will be. Better to report the page unread than
        # to hold this worker until the site's clock frees up.
        ctx.log.info("crawl skipped a page: host busy", url=url)
        return None
    try:
        page = await fetcher.fetch(url)
    except AccessRestrictedError as exc:
        ctx.log.info("crawl refused", url=url, reason=str(exc), error_class="access_restricted")
        return None
    except InvalidInputError as exc:
        # The SSRF guard, or a URL that was never fetchable. Recorded and dropped: no retry can
        # make a private address public.
        ctx.log.info("crawl refused an unsafe url", url=url, reason=str(exc))
        return None
    if not page.ok or not page.body:
        ctx.log.info("crawl read nothing useful", url=url, status=page.status)
        return None
    return page


async def _sitemap(
    ctx: JobContext, homepage_url: str, *, fetcher: CrawlFetcher, frontier: Frontier
) -> list[str]:
    """`/sitemap.xml`, if the site has one. Absent is the common case and costs nothing."""
    page = await _read(
        ctx, urljoin(homepage_url, "/sitemap.xml"), fetcher=fetcher, frontier=frontier
    )
    return sitemap_urls(page.body) if page is not None else []


def _research_job_id(ctx: JobContext) -> str:
    job_id = ctx.envelope.research_job_id
    if job_id is None:
        raise InvalidInputError("a crawl task must belong to a research job")
    return str(job_id)
