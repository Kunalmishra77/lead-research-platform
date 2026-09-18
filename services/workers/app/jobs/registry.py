"""Job type -> handler mapping (`<domain>.<action>`, docs/03 naming)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from leadforge_contracts.job_envelope import JobEnvelope

from app.jobs.context import JobContext
from app.jobs.errors import ErrorClass
from app.jobs.progress import Stage

Handler = Callable[[JobContext], Awaitable[None]]
#: Called once when a job ends without success (dead-lettered or paused): persist the outcome.
FailureHook = Callable[[JobEnvelope, ErrorClass, str, int | None], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Registration:
    handler: Handler
    #: Stage reported on final failure/pause events emitted by the consumer.
    stage: Stage
    on_failure: FailureHook | None = None


class HandlerRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, Registration] = {}

    def register(
        self, job_type: str, *, stage: Stage, on_failure: FailureHook | None = None
    ) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            if job_type in self._entries:
                raise ValueError(f"handler for {job_type} already registered")
            self._entries[job_type] = Registration(handler, stage, on_failure)
            return handler

        return decorator

    def get(self, job_type: str) -> Handler | None:
        entry = self._entries.get(job_type)
        return None if entry is None else entry.handler

    def stage_for(self, job_type: str) -> Stage:
        entry = self._entries.get(job_type)
        return "queued" if entry is None else entry.stage

    def failure_hook(self, job_type: str) -> FailureHook | None:
        entry = self._entries.get(job_type)
        return None if entry is None else entry.on_failure

    @property
    def job_types(self) -> list[str]:
        return sorted(self._entries)
