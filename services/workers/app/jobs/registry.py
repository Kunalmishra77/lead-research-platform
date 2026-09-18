"""Job type -> handler mapping (`<domain>.<action>`, docs/03 naming)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.jobs.context import JobContext
from app.jobs.progress import Stage

Handler = Callable[[JobContext], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Registration:
    handler: Handler
    #: Stage reported on final failure/pause events emitted by the consumer.
    stage: Stage


class HandlerRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, Registration] = {}

    def register(self, job_type: str, *, stage: Stage) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            if job_type in self._entries:
                raise ValueError(f"handler for {job_type} already registered")
            self._entries[job_type] = Registration(handler, stage)
            return handler

        return decorator

    def get(self, job_type: str) -> Handler | None:
        entry = self._entries.get(job_type)
        return None if entry is None else entry.handler

    def stage_for(self, job_type: str) -> Stage:
        entry = self._entries.get(job_type)
        return "queued" if entry is None else entry.stage

    @property
    def job_types(self) -> list[str]:
        return sorted(self._entries)
