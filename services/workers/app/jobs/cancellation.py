"""The cancel flag workers check before spending anything (ADR-0008).

`POST /app/research/:id/cancel` marks the job cancelled and releases its credit reservation. By
then the `research.plan` envelope is already in a stream and, once discovery fans out, so are
many more; a message a consumer has claimed cannot be deleted. A worker that kept going after
the release would deliver leads the ledger no longer covers — unmetered spend, which CLAUDE.md's
budget rule forbids outright.

So the API raises a Redis flag *before* it releases anything, and workers read it: after claiming
an envelope, before starting each task, and before writing a `usage_events` row. It is advisory
and may expire; `research_jobs.status` stays the source of truth for the UI.

Reading it is a local Redis GET, so checking often costs nothing worth counting — and the check
immediately before a paid call is the one that decides whether the user is billed for work they
have already cancelled.
"""

from redis.asyncio import Redis

#: Written by the API (`cancelKey` in apps/api/src/modules/research/research.service.ts). The two
#: must agree exactly, so this is the same string in the same shape, not a near-miss.
CANCEL_KEY = "research:cancelled:{job_id}"


class JobCancelledError(Exception):
    """Raised to unwind out of a job the user has stopped.

    Not one of the classified job errors: a cancelled job did not fail, and reporting it as
    `transient` would have the runner retry work nobody is paying for any more.
    """

    def __init__(self, job_id: str) -> None:
        super().__init__(f"research job {job_id} was cancelled")
        self.job_id = job_id


async def is_cancelled(redis: Redis, research_job_id: str | None) -> bool:
    """True when the user has stopped this job. False for work with no research job behind it."""
    if not research_job_id:
        return False
    return bool(await redis.exists(CANCEL_KEY.format(job_id=research_job_id)))


async def raise_if_cancelled(redis: Redis, research_job_id: str | None) -> None:
    if await is_cancelled(redis, research_job_id):
        raise JobCancelledError(str(research_job_id))
