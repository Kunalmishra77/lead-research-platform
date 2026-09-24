"""app.research_tasks: writing a plan (task 2.10) and running it (task 2.11).

`app_worker` may UPDATE a task's run state — status, attempts, cost, output, error — and nothing
else (migration 0013): not its budget, its input, its type or its parent. So a plan is decided
once and then only executed, and nothing here can pretend otherwise.

Inserts are idempotent against `research_tasks_job_key_uniq` (migration 0018). A redelivered
`research.plan` envelope re-reads the plan it already bought instead of buying it again.
"""

import json
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from uuid6 import uuid7

from app.db.tenant import tenant_transaction
from app.planner.plan import PlannedTask

#: `DO UPDATE` rather than `DO NOTHING` so the conflicting row's id comes back: with `DO NOTHING`
#: a redelivery returns no row at all and there is nothing to fan out. `attempts` is assigned to
#: itself because it is one of the few columns app_worker may update (migration 0013) — assigning
#: to `type` or `input` would be refused by the grant, which is the protection we want.
#:
#: It is a real UPDATE, not a no-op: Postgres writes a new tuple version and the
#: `research_tasks_updated_at` BEFORE UPDATE trigger sets `updated_at = now()` unconditionally.
#: So a redelivered plan leaves every already-planned task with a fresh `updated_at` while its
#: status and budget are untouched. Worth knowing before the 2.13 admin view reads that column
#: as "when this task last did something".
_UPSERT = """
insert into app.research_tasks
    (id, org_id, research_job_id, parent_task_id, type, input, credit_budget)
values
    (:id, :org_id, :job_id, :parent_id, :type, cast(:input as jsonb), :credit_budget)
on conflict (research_job_id, task_key)
    do update set attempts = app.research_tasks.attempts
returning id, (xmax = 0) as inserted
"""


class ResearchTasksRepo(Protocol):
    async def save_plan(
        self, org_id: str, job_id: str, tasks: list[PlannedTask]
    ) -> list["SavedTask"]: ...
    async def mark_running(self, org_id: str, task_id: str, attempt: int) -> bool: ...
    async def mark_completed(
        self, org_id: str, task_id: str, output: dict[str, Any], cost_micros: int
    ) -> bool: ...
    async def mark_failed(
        self, org_id: str, task_id: str, error_class: str, cost_micros: int
    ) -> bool: ...
    async def mark_cancelled(self, org_id: str, task_id: str) -> bool: ...


class SavedTask:
    """A planned task and the row that now holds it."""

    __slots__ = ("id", "inserted", "task")

    def __init__(self, task_id: str, task: PlannedTask, *, inserted: bool) -> None:
        self.id = task_id
        self.task = task
        #: False when this task was already planned, i.e. the envelope was redelivered.
        self.inserted = inserted


class SqlResearchTasksRepo:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def _update(self, org_id: str, sql: str, params: dict[str, Any]) -> bool:
        """Never moves a task that has already stopped.

        A redelivered envelope, or a message reclaimed after a worker died, must not set a
        finished task back to running and must not overwrite the first reason it failed.
        Returns whether a row actually changed, so a caller can tell "done" from "not mine".
        """
        async with tenant_transaction(self._engine, org_id) as conn:
            result = await conn.execute(text(sql), params)
            return bool(result.rowcount)

    async def mark_running(self, org_id: str, task_id: str, attempt: int) -> bool:
        return await self._update(
            org_id,
            "update app.research_tasks set status = 'running', attempts = :attempt,"
            " started_at = coalesce(started_at, now())"
            " where id = :id and status not in ('completed', 'failed', 'cancelled')",
            {"id": task_id, "attempt": attempt},
        )

    async def mark_completed(
        self, org_id: str, task_id: str, output: dict[str, Any], cost_micros: int
    ) -> bool:
        """What the task produced, so the admin view and the critic loop can read it later."""
        return await self._update(
            org_id,
            "update app.research_tasks set status = 'completed',"
            " output = cast(cast(:output as text) as jsonb), cost_micros = :cost,"
            " finished_at = now()"
            " where id = :id and status not in ('completed', 'failed', 'cancelled')",
            {"id": task_id, "output": json.dumps(output, sort_keys=True), "cost": cost_micros},
        )

    async def mark_failed(
        self, org_id: str, task_id: str, error_class: str, cost_micros: int
    ) -> bool:
        """Records the cost as well as the failure: a task that died mid-way still spent money."""
        return await self._update(
            org_id,
            "update app.research_tasks set status = 'failed',"
            " error_class = cast(:error_class as app.error_class), cost_micros = :cost,"
            " finished_at = now()"
            " where id = :id and status not in ('completed', 'failed', 'cancelled')",
            {"id": task_id, "error_class": error_class, "cost": cost_micros},
        )

    async def mark_cancelled(self, org_id: str, task_id: str) -> bool:
        """ADR-0008: on a raised flag a worker stops and marks its task cancelled."""
        return await self._update(
            org_id,
            "update app.research_tasks set status = 'cancelled', finished_at = now()"
            " where id = :id and status not in ('completed', 'failed', 'cancelled')",
            {"id": task_id},
        )

    async def save_plan(
        self, org_id: str, job_id: str, tasks: list[PlannedTask]
    ) -> list[SavedTask]:
        """Writes the plan and returns each task with its id, in plan order.

        One transaction: a half-written plan is a job that searches part of a market and then
        reports success. A task already present keeps its original id, so a redelivery fans out
        the same work rather than a second copy of it.
        """
        saved: list[SavedTask] = []
        async with tenant_transaction(self._engine, org_id) as conn:
            for task in tasks:
                row = (
                    await conn.execute(
                        text(_UPSERT),
                        {
                            "id": str(uuid7()),
                            "org_id": org_id,
                            "job_id": job_id,
                            # Flat in v1: nothing the planner emits has a parent yet, and
                            # `research_tasks` is a tree rather than a DAG, so the column arrives
                            # with the first task that actually needs it.
                            "parent_id": None,
                            "type": task.type,
                            "input": json.dumps(task.input, sort_keys=True),
                            "credit_budget": task.credit_budget,
                        },
                    )
                ).one()
                saved.append(SavedTask(str(row[0]), task, inserted=bool(row[1])))
        return saved
