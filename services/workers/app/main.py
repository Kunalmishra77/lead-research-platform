"""Worker process: `uv run python -m app.main`. Consumes the pools listed in WORKER_POOLS."""

import asyncio
import contextlib
import os
import signal
import socket
import sys

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai.factory import build_gateway
from app.config import Settings, get_settings
from app.connectors.factory import build_connectors, close_clients
from app.connectors.http_client import CallCost, ConnectorHttpClient
from app.crawl.fetcher import CrawlFetcher, crawl_http_client
from app.crawl.frontier import Frontier
from app.crawl.rawstore import RETENTION_DAYS, LocalRawStore, prune
from app.crawl.robots import RobotsFetcher, RobotsPolicy
from app.db.engine import create_engine
from app.db.graph import SqlGraphRepo
from app.db.job_runs import SqlJobRunsRepo
from app.db.raw_documents import SqlRawDocuments
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
from app.metering.usage import SqlUsageRecorder
from app.telemetry import configure_tracing

log = get_logger("leadforge.workers")

#: Crawling is free, so nothing is metered and no spend is reserved.
ROBOTS_COST = CallCost(meter="crawl_robots", cost_micros=0)


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


def _robots_fetch(client: ConnectorHttpClient) -> RobotsFetcher:
    """Fetches robots.txt through the crawl client, without consulting robots.txt to do it.

    A 4xx or 5xx is an answer, not an error, so the status is returned rather than raised on:
    `RobotsPolicy` reads 4xx as "no rules" and 5xx as "cannot say", and those mean opposite
    things.
    """

    async def fetch(url: str) -> tuple[int, bytes]:
        response = await client.get(url, cost=ROBOTS_COST)
        return response.status_code, response.content

    return fetch


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
    # Without a recorder every paid call raises: NullUsageRecorder refuses anything that
    # costs money, which is the right default for tests and the wrong one for production.
    usage = SqlUsageRecorder(engine)
    connectors, connector_clients = build_connectors(
        settings=settings, redis=redis, usage=usage, log=log
    )
    # One client for every site we crawl, built so `follow_redirects` cannot be left on:
    # CrawlFetcher checks robots and the SSRF guard on each hop, which only works if httpx has
    # not already followed them.
    crawl_client = crawl_http_client()
    crawler = CrawlFetcher(
        crawl_client,
        RobotsPolicy(redis, _robots_fetch(crawl_client)),
    )
    # One instance, shared: it is a read-through cache over the two seeded lookup tables, and a
    # second copy would load the geography and the taxonomy again per worker for nothing.
    reference = ReferenceData(engine)
    registry = build_registry(
        job_runs=SqlJobRunsRepo(engine),
        gateway=gateway,
        connectors=connectors,
        reference=reference,
        research_tasks=SqlResearchTasksRepo(engine),
        research_jobs=SqlResearchJobsRepo(engine),
        graph=SqlGraphRepo(engine, geography=reference),
        raw_documents=_raw_documents(settings, engine),
        usage=usage,
        crawler=crawler,
        frontier=Frontier(redis),
        discovery_pool=settings.DISCOVERY_POOL,
        crawl_pool=settings.CRAWL_POOL,
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


def _raw_documents(settings: Settings, engine: AsyncEngine) -> SqlRawDocuments | None:
    """The raw store, when this host has somewhere to put pages (ADR-0019).

    None when `RAW_STORE_PATH` is unset, and the crawler then keeps no copies and still records
    nothing -- which is the right behaviour for a worker that was deployed without the volume,
    because a crawl that cannot save a page is still a crawl that found an email.
    """
    root = settings.RAW_STORE_PATH
    if root is None:
        log.warning("RAW_STORE_PATH is not set; crawled pages will not be kept")
        return None
    root.mkdir(parents=True, exist_ok=True)

    # docs/04 keeps bodies 90 days. The database sweeper runs on pg_cron and can only delete
    # rows; the files are on this host's disk and only a process here can unlink them. Done at
    # startup because there is no worker-side scheduler yet and a deploy happens often enough to
    # bound the store -- `python -m app.crawl prune` is the same sweep by hand. It is
    # synchronous and runs before the consumers start, so it never walks a directory tree while
    # a crawl is waiting on the event loop.
    pruned = prune(root)
    if pruned.files:
        log.info(
            "pruned the raw store",
            files=pruned.files,
            megabytes=round(pruned.bytes / 1_000_000, 1),
            months_removed=pruned.months_removed,
            keep_days=RETENTION_DAYS,
        )

    log.info("raw store ready", path=str(root))
    return SqlRawDocuments(engine, LocalRawStore(root))


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
