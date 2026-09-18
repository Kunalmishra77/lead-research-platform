"""Status updates for app.job_runs (created by the API; updated by workers under RLS).

Updates never touch a finished row (completed/failed/cancelled): a redelivery after completion
cannot set a job back to running. Each method returns whether a row changed.
"""

import json
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.tenant import tenant_transaction


class JobRunsRepo(Protocol):
    async def mark_running(self, org_id: str, job_id: str, attempt: int) -> bool: ...
    async def mark_completed(self, org_id: str, job_id: str, result: dict[str, Any]) -> bool: ...
    async def mark_failed(
        self, org_id: str, job_id: str, error_class: str, error: str, attempts: int | None
    ) -> bool: ...


class SqlJobRunsRepo:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def _update(self, org_id: str, sql: str, params: dict[str, Any]) -> bool:
        async with tenant_transaction(self._engine, org_id) as conn:
            result = await conn.execute(text(sql), params)
            return bool(result.rowcount)

    async def mark_running(self, org_id: str, job_id: str, attempt: int) -> bool:
        return await self._update(
            org_id,
            "update app.job_runs set status = 'running', attempts = :attempt,"
            " started_at = coalesce(started_at, now()) where id = :id"
            " and status not in ('completed', 'failed', 'cancelled')",
            {"id": job_id, "attempt": attempt},
        )

    async def mark_completed(self, org_id: str, job_id: str, result: dict[str, Any]) -> bool:
        return await self._update(
            org_id,
            "update app.job_runs set status = 'completed', result = cast(:result as jsonb),"
            " error_class = null, error = null, finished_at = now() where id = :id"
            " and status not in ('completed', 'failed', 'cancelled')",
            {"id": job_id, "result": json.dumps(result)},
        )

    async def mark_failed(
        self, org_id: str, job_id: str, error_class: str, error: str, attempts: int | None
    ) -> bool:
        return await self._update(
            org_id,
            "update app.job_runs set status = 'failed',"
            " error_class = cast(:error_class as app.error_class), error = :error,"
            " attempts = coalesce(:attempts, attempts), finished_at = now() where id = :id"
            " and status not in ('completed', 'failed', 'cancelled')",
            {"id": job_id, "error_class": error_class, "error": error, "attempts": attempts},
        )
