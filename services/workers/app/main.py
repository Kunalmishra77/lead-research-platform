"""Worker process: `uv run python -m app.main`. Consumes the pools listed in WORKER_POOLS."""

import asyncio
import contextlib
import os
import signal
import socket
import sys

from redis.asyncio import Redis

from app.ai.factory import build_gateway
from app.config import Settings, get_settings
from app.connectors.factory import build_connectors, close_clients
from app.db.engine import create_engine
from app.db.job_runs import SqlJobRunsRepo
from app.db.reference import ReferenceData
from app.db.research_jobs import SqlResearchJobsRepo
from app.db.research_tasks import SqlResearchTasksRepo
from app.devdns import install_dev_dns
from app.error_reporting import configure_error_reporting, report_exception
from app.handlers import build_registry
from app.jobs.consumer import ConsumerSettings, StreamConsumer
from app.jobs.envelope import stream_for
from app.jobs.retry import promote_due
from app.logging import configure_logging, get_logger
from app.telemetry import configure_tracing

log = get_logger("leadforge.workers")


async def _promote_loop(redis: Redis, streams: list[str], stop: asyncio.Event) -> None:
    """Moves due retries from each stream's delayed ZSET back onto the stream."""
    while not stop.is_set():
        try:
            for stream in streams:
                await promote_due(redis, stream)
        except Exception as exc:
            log.exception("delayed promotion failed", error_class="transient")
            report_exception(exc, error_class="transient", component="retry_promotion")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1.0)


async def run(settings: Settings, stop: asyncio.Event) -> None:
    redis = Redis.from_url(
        str(settings.REDIS_URL), socket_connect_timeout=5, health_check_interval=30
    )
    engine = create_engine(settings)
    gateway = None
    if settings.OPENAI_API_KEY:
        gateway = build_gateway(redis=redis, settings=settings, engine=engine, log=log)
    else:
        # The process still runs: only the pools whose jobs need a model are left unhandled,
        # and the consumer reports that per job rather than failing at startup (ADR-0009).
        log.warning("OPENAI_API_KEY is not set; ai handlers are not registered")
    connectors, connector_clients = build_connectors(settings=settings, redis=redis)
    registry = build_registry(
        job_runs=SqlJobRunsRepo(engine),
        gateway=gateway,
        connectors=connectors,
        reference=ReferenceData(engine),
        research_tasks=SqlResearchTasksRepo(engine),
        research_jobs=SqlResearchJobsRepo(engine),
        discovery_pool=settings.DISCOVERY_POOL,
    )
    name = settings.WORKER_NAME or f"{socket.gethostname()}-{os.getpid()}"
    consumers = [
        StreamConsumer(
            redis,
            registry,
            ConsumerSettings(
                stream=stream_for(pool),
                group=f"workers:{pool}",
                consumer=name,
                max_attempts=settings.JOB_MAX_ATTEMPTS,
                visibility_timeout_ms=settings.JOB_VISIBILITY_TIMEOUT_MS,
                batch_size=settings.JOB_CONCURRENCY,
                retry_base_delay_s=settings.JOB_RETRY_BASE_DELAY_MS / 1000,
                reclaim_every_s=settings.JOB_RECLAIM_INTERVAL_MS / 1000,
            ),
            log,
        )
        for pool in settings.pools
    ]
    log.info("workers starting", pools=settings.pools, consumer=name, job_types=registry.job_types)
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(_promote_loop(redis, [stream_for(p) for p in settings.pools], stop))
            for consumer in consumers:
                tasks.create_task(consumer.run(stop))
    finally:
        await close_clients(connector_clients)
        await redis.aclose()
        await engine.dispose()
        log.info("workers stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    install_dev_dns(enabled=settings.DEV_DNS_OVER_HTTPS, node_env=settings.NODE_ENV)
    configure_tracing(settings.OTEL_EXPORTER_OTLP_ENDPOINT, settings.NODE_ENV)
    configure_error_reporting(
        settings.SENTRY_DSN_WORKERS,
        settings.SENTRY_ENVIRONMENT or settings.NODE_ENV,
        settings.SENTRY_TRACES_SAMPLE_RATE,
    )
    if sys.platform != "win32":
        import uvloop  # noqa: PLC0415 - optional, Linux/macOS only (ADR-0002)

        uvloop.install()

    async def runner() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            # Not supported on Windows; Ctrl+C then raises KeyboardInterrupt below.
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        await run(settings, stop)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(runner())


if __name__ == "__main__":
    main()
