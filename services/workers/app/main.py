"""Worker process: `uv run python -m app.main`. Consumes the pools listed in WORKER_POOLS."""

import asyncio
import contextlib
import os
import signal
import socket
import sys

from redis.asyncio import Redis

from app.config import Settings, get_settings
from app.devdns import install_dev_dns
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
        except Exception:
            log.exception("delayed promotion failed", error_class="transient")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1.0)


async def run(settings: Settings, stop: asyncio.Event) -> None:
    redis = Redis.from_url(
        str(settings.REDIS_URL), socket_connect_timeout=5, health_check_interval=30
    )
    registry = build_registry()
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
        await redis.aclose()
        log.info("workers stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    install_dev_dns(enabled=settings.DEV_DNS_OVER_HTTPS, node_env=settings.NODE_ENV)
    configure_tracing(settings.OTEL_EXPORTER_OTLP_ENDPOINT, settings.NODE_ENV)
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
