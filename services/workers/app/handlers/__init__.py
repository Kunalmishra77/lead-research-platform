"""Job handlers. `build_registry()` wires every handler module into one registry."""

from app.ai.gateway import AiGateway
from app.db.job_runs import JobRunsRepo
from app.handlers.research import register_research_handlers
from app.handlers.system import register_system_handlers
from app.jobs.registry import HandlerRegistry


def build_registry(*, job_runs: JobRunsRepo, gateway: AiGateway | None = None) -> HandlerRegistry:
    """Without a gateway the AI handlers are left out, so a key-less worker still runs the
    rest. A pool that needs them then has no handler, which the consumer reports per job."""
    registry = HandlerRegistry()
    register_system_handlers(registry, job_runs)
    if gateway is not None:
        register_research_handlers(registry, gateway)
    return registry
