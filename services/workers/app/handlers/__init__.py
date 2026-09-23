"""Job handlers. `build_registry()` wires every handler module into one registry."""

from app.ai.gateway import AiGateway
from app.connectors.registry import ConnectorRegistry
from app.db.job_runs import JobRunsRepo
from app.db.reference import ReferenceData
from app.db.research_jobs import ResearchJobsRepo
from app.db.research_tasks import ResearchTasksRepo
from app.handlers.planner import DEFAULT_DISCOVERY_POOL, register_planner_handlers
from app.handlers.research import register_research_handlers
from app.handlers.system import register_system_handlers
from app.jobs.registry import HandlerRegistry


def build_registry(
    *,
    job_runs: JobRunsRepo,
    gateway: AiGateway | None = None,
    connectors: ConnectorRegistry | None = None,
    reference: ReferenceData | None = None,
    research_tasks: ResearchTasksRepo | None = None,
    research_jobs: ResearchJobsRepo | None = None,
    discovery_pool: str = DEFAULT_DISCOVERY_POOL,
) -> HandlerRegistry:
    """Without a gateway the AI handlers are left out, so a key-less worker still runs the
    rest. A pool that needs them then has no handler, which the consumer reports per job.

    The planner is the exception: its one model call already degrades to planning the request as
    written, so it registers with or without a gateway. What it cannot do without is somewhere to
    look places up and somewhere to record what it decided — half a planner, able to spend a
    model call but not to keep the answer, is worse than none.
    """
    registry = HandlerRegistry()
    register_system_handlers(registry, job_runs)
    if gateway is not None:
        register_research_handlers(registry, gateway)
    if (
        connectors is not None
        and reference is not None
        and research_tasks is not None
        and research_jobs is not None
    ):
        register_planner_handlers(
            registry,
            gateway=gateway,
            connectors=connectors,
            reference=reference,
            tasks=research_tasks,
            jobs=research_jobs,
            pool=discovery_pool,
        )
    return registry
