"""Progress events on Redis pub/sub `progress:{job_id}` (docs/01). Final state lives in Postgres."""

from datetime import UTC, datetime
from typing import Literal

from leadforge_contracts.progress_event import ProgressEvent
from redis.asyncio import Redis

Stage = Literal[
    "queued",
    "planning",
    "discovery",
    "crawling",
    "extracting",
    "resolving",
    "verifying",
    "enriching",
    "scoring",
    "exporting",
    "ping",
]
Status = Literal["running", "completed", "failed", "paused", "cancelled"]
ErrorClassName = Literal[
    "transient",
    "rate_limited",
    "access_restricted",
    "parse_failed",
    "invalid_input",
    "budget_exhausted",
]


def progress_channel(job_id: str) -> str:
    return f"progress:{job_id}"


class ProgressPublisher:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(
        self,
        *,
        job_id: str,
        org_id: str | None,
        trace_id: str,
        stage: Stage,
        status: Status,
        counts: dict[str, int] | None = None,
        credits_used: int = 0,
        message: str | None = None,
        error_class: ErrorClassName | None = None,
    ) -> ProgressEvent:
        event = ProgressEvent.model_validate(
            {
                "event_version": 1,
                "job_id": job_id,
                "org_id": org_id,
                "trace_id": trace_id,
                "stage": stage,
                "status": status,
                "counts": counts or {},
                "credits_used": credits_used,
                "message": message,
                "error_class": error_class,
                "ts": datetime.now(UTC),
            }
        )
        await self._redis.publish(progress_channel(job_id), event.to_wire())
        return event
