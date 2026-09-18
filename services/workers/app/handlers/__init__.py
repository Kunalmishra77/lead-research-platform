"""Job handlers. `build_registry()` wires every handler module into one registry."""

from app.db.job_runs import JobRunsRepo
from app.handlers.system import register_system_handlers
from app.jobs.registry import HandlerRegistry


def build_registry(*, job_runs: JobRunsRepo) -> HandlerRegistry:
    registry = HandlerRegistry()
    register_system_handlers(registry, job_runs)
    return registry
