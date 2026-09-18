"""What a handler receives besides the envelope."""

from dataclasses import dataclass, field

import structlog
from leadforge_contracts.job_envelope import JobEnvelope
from redis.asyncio import Redis

from app.jobs.progress import ProgressPublisher


@dataclass(frozen=True, slots=True)
class JobContext:
    envelope: JobEnvelope
    redis: Redis
    progress: ProgressPublisher
    log: structlog.stdlib.BoundLogger
    #: Stream message id (useful for logs; not stable across retries).
    message_id: str
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def job_id(self) -> str:
        return str(self.envelope.job_id)

    @property
    def org_id(self) -> str | None:
        return None if self.envelope.org_id is None else str(self.envelope.org_id)
