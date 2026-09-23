"""Metering for paid calls (CLAUDE.md: every paid API/LLM/browser call records a usage event).

Shared by the connectors and the AI gateway, so it belongs to neither.

`unit_key` makes a retry free: the same call within one job is recorded once (docs/11). Credits for
delivered leads are booked separately by the executor; these rows carry internal cost only.

A paid call that cannot be attributed to an org and a job is a bug, not a cheaper call: it fails
loudly rather than spending money off the books.
"""

from hashlib import sha256
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from uuid6 import uuid7

from app.db.tenant import tenant_transaction
from app.jobs.errors import InvalidInputError


def call_unit_key(source_key: str, url: str, body: bytes | None = None) -> str:
    """Stable key for one outbound call, so retries never double-count the cost."""
    digest = sha256(url.encode("utf-8"))
    if body:
        digest.update(body)
    return f"{source_key}:{digest.hexdigest()[:32]}"


class UsageRecorder(Protocol):
    async def record(
        self,
        *,
        org_id: str,
        research_job_id: str | None,
        meter: str,
        unit_key: str,
        cost_micros: int,
        units: int = 1,
        credits: int = 0,
    ) -> bool:
        """Records one metered unit. Returns False when it was already recorded."""
        ...


class SqlUsageRecorder:
    """Writes app.usage_events + app.usage_unit_keys in one transaction (ADR-0003)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record(
        self,
        *,
        org_id: str,
        research_job_id: str | None,
        meter: str,
        unit_key: str,
        cost_micros: int,
        units: int = 1,
        credits: int = 0,
    ) -> bool:
        if research_job_id is None:
            # usage_events.research_job_id is nullable, but usage_unit_keys needs it for the
            # dedupe key, so an unattributed call could be charged twice. Refuse it instead.
            raise InvalidInputError(f"usage for meter {meter} has no research job to attribute to")
        event_id = str(uuid7())
        async with tenant_transaction(self._engine, org_id) as conn:
            claimed = await conn.execute(
                text(
                    "insert into app.usage_unit_keys"
                    " (org_id, research_job_id, meter, unit_key, usage_event_id)"
                    " values (:org, :job, :meter, :key, :event)"
                    " on conflict do nothing"
                ),
                {
                    "org": org_id,
                    "job": research_job_id,
                    "meter": meter,
                    "key": unit_key,
                    "event": event_id,
                },
            )
            if not claimed.rowcount:
                return False
            await conn.execute(
                text(
                    "insert into app.usage_events"
                    " (id, org_id, research_job_id, meter, units, credits, cost_micros, unit_key)"
                    " values (:id, :org, :job, :meter, :units, :credits, :cost, :key)"
                ),
                {
                    "id": event_id,
                    "org": org_id,
                    "job": research_job_id,
                    "meter": meter,
                    "units": units,
                    "credits": credits,
                    "cost": cost_micros,
                    "key": unit_key,
                },
            )
        return True


class NullUsageRecorder:
    """For free sources and tests: records nothing, and refuses anything that costs money."""

    async def record(
        self,
        *,
        org_id: str,
        research_job_id: str | None,
        meter: str,
        unit_key: str,
        cost_micros: int,
        units: int = 1,
        credits: int = 0,
    ) -> bool:
        if cost_micros > 0:
            raise InvalidInputError(
                f"meter {meter} costs {cost_micros} micros but this client has no usage recorder"
            )
        return False
