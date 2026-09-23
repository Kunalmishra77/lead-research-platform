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

# Every statement below ends with `status not in ('completed', 'failed', 'cancelled')`: a job
# that has already stopped stays stopped. A redelivered envelope must not reopen a job the user
# cancelled, nor overwrite the first reason it failed with a later, vaguer one.


class ResearchJobsRepo(Protocol):
    async def mark_failed(
        self, org_id: str, job_id: str, error_class: str, message: str
    ) -> bool: ...
    async def mark_cancelled(self, org_id: str, job_id: str) -> bool: ...


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
