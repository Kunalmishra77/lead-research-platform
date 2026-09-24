"""Status updates for app.research_jobs (created by the API; run state owned by workers).

The worker's grant is narrow — `UPDATE (status, progress, error_class, started_at, finished_at)`
(migration 0015) — and deliberately excludes the credit columns. So this can say a job stopped
and why, and it cannot touch what the job cost.

**It cannot release the reservation either.** `app.credit_settle` is granted to `app_api` only
(migration 0015), so marking a job failed here makes the failure visible and unblocks the UI, but
the credits the API reserved stay reserved until something with that grant settles them. That gap
is real and is recorded in PROGRESS.md against task 2.11 rather than papered over here.
"""

from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.tenant import tenant_transaction
from app.jobs.errors import InvalidInputError

#: The only counters a job's progress may carry. `add_progress` interpolates these names into
#: its statement, so the set is closed on purpose.
PROGRESS_COUNTERS: frozenset[str] = frozenset({"candidates", "leads", "values"})

# Every statement below ends with `status not in ('completed', 'failed', 'cancelled')`: a job
# that has already stopped stays stopped. A redelivered envelope must not reopen a job the user
# cancelled, nor overwrite the first reason it failed with a later, vaguer one.


class ResearchJobsRepo(Protocol):
    async def mark_failed(
        self, org_id: str, job_id: str, error_class: str, message: str
    ) -> bool: ...
    async def mark_cancelled(self, org_id: str, job_id: str) -> bool: ...
    async def mark_running(self, org_id: str, job_id: str) -> bool: ...
    async def add_progress(self, org_id: str, job_id: str, counts: dict[str, int]) -> bool: ...
    async def finish_if_done(self, org_id: str, job_id: str) -> bool: ...


class SqlResearchJobsRepo:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def _update(self, org_id: str, sql: str, params: dict[str, Any]) -> bool:
        async with tenant_transaction(self._engine, org_id) as conn:
            result = await conn.execute(text(sql), params)
            return bool(result.rowcount)

    async def mark_failed(self, org_id: str, job_id: str, error_class: str, message: str) -> bool:
        """Ends a job that cannot run, so it does not sit at `planning` for ever.

        The reason goes into `progress`, which the job page reads: "no source can find businesses
        for this" is something the user can act on, where a job stuck in planning is not.
        """
        return await self._update(
            org_id,
            "update app.research_jobs set status = 'failed',"
            " error_class = cast(:error_class as app.error_class),"
            " progress = progress || jsonb_build_object('message', cast(:message as text)),"
            " finished_at = now()"
            " where id = :id and status not in ('completed', 'failed', 'cancelled')",
            {"id": job_id, "error_class": error_class, "message": message},
        )

    async def mark_running(self, org_id: str, job_id: str) -> bool:
        """Moves a planned job to running the first time one of its tasks starts."""
        return await self._update(
            org_id,
            "update app.research_jobs set status = 'running',"
            " started_at = coalesce(started_at, now())"
            " where id = :id and status in ('queued', 'planning')",
            {"id": job_id},
        )

    async def add_progress(self, org_id: str, job_id: str, counts: dict[str, int]) -> bool:
        """Adds to the durable counters behind the job page.

        Additive rather than assigned, because a job's tasks run in parallel across workers and
        each only knows its own share. The SSE stream is live but lossy — a browser that connects
        late, or reconnects, reads these (`research_jobs.progress`, `progress-stream.ts`).
        """
        if not counts:
            return False
        unknown = set(counts) - PROGRESS_COUNTERS
        if unknown:
            # The counter names are interpolated into SQL, so they may only ever be names this
            # module already knows. Checked rather than escaped: a new counter should be a
            # deliberate addition here, not whatever a caller happened to pass.
            raise InvalidInputError(f"unknown progress counters: {sorted(unknown)}")
        additions = " || ".join(
            f"jsonb_build_object('{name}',"
            f" coalesce((progress->>'{name}')::bigint, 0) + :{name}::bigint)"
            for name in counts
        )
        return await self._update(
            org_id,
            f"update app.research_jobs set progress = progress || {additions}"  # noqa: S608
            " where id = :id and status not in ('completed', 'failed', 'cancelled')",
            {"id": job_id, **counts},
        )

    async def finish_if_done(self, org_id: str, job_id: str) -> bool:
        """Completes the job once no task of it is still queued or running.

        Every task checks this as it finishes, and whichever is genuinely last wins: the update
        is conditional on the same emptiness it just observed, so two tasks finishing together
        cannot both complete the job, and a task that finishes while others still run does
        nothing. Without it a job whose tasks all succeeded would sit at `running` for ever and
        its credit reservation would never be settled.
        """
        return await self._update(
            org_id,
            "update app.research_jobs set status = 'completed', finished_at = now()"
            " where id = :id and status = 'running'"
            "   and not exists ("
            "     select 1 from app.research_tasks"
            "     where research_job_id = :id and status in ('queued', 'running'))",
            {"id": job_id},
        )

    async def mark_cancelled(self, org_id: str, job_id: str) -> bool:
        """Records what the cancel flag already told us (ADR-0008).

        The API sets `status = 'cancelled'` on its side of the cancel, so this is usually a
        no-op. It exists for the other order: the flag is advisory and may be seen by a worker
        before the API's own write lands.
        """
        return await self._update(
            org_id,
            "update app.research_jobs set status = 'cancelled', finished_at = now()"
            " where id = :id and status not in ('completed', 'failed', 'cancelled')",
            {"id": job_id},
        )
