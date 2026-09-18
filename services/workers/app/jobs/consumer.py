"""Redis Streams consumer (ADR-0001): consumer groups, retries, DLQ, reclaim of stale messages.

Lifecycle per message (docs/12): validate envelope -> idempotency -> run handler -> ack. Budget
pre-checks and usage recording belong to the handlers' metered clients (Phase 2, docs/11).
- success: idempotency key -> done, XACK.
- retryable failure (transient / rate_limited): new envelope with attempt + 1 on the delayed ZSET
  and XACK of the old message in one MULTI; after `max_attempts` attempts -> `dlq:<stream>`.
- terminal failure (access_restricted, parse_failed, invalid_input): DLQ immediately.
- budget_exhausted: job paused (progress event), acknowledged, not dead-lettered.
- while a handler runs, a heartbeat resets the message idle time (XCLAIM JUSTID, no delivery
  count) and refreshes the idempotency claim, so healthy long jobs are never reclaimed.
- worker crash: the message goes idle and another consumer reclaims it (XAUTOCLAIM); the claim
  token equals the message id, so the reclaimer takes the job over. Messages delivered more than
  `max_attempts` times are dead-lettered (crash loops).
"""

import asyncio
import contextlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import structlog
from leadforge_contracts.job_envelope import JobEnvelope
from opentelemetry import trace
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.jobs.context import JobContext
from app.jobs.envelope import ENVELOPE_FIELD, dlq_for, parse_envelope
from app.jobs.errors import RETRYABLE, ErrorClass, InvalidInputError, classify
from app.jobs.idempotency import IdempotencyGuard, idempotency_key
from app.jobs.progress import ProgressPublisher, Status
from app.jobs.redact import redact
from app.jobs.registry import HandlerRegistry
from app.jobs.retry import STREAM_MAXLEN, backoff_seconds, queue_retry

_tracer = trace.get_tracer("leadforge.workers.jobs")

Fields = dict[bytes | str, bytes | str]
#: XREADGROUP reply: [(stream, [(message_id, fields), ...]), ...]
StreamBatch = list[tuple[bytes, list[tuple[bytes, Fields]]]]


@dataclass(frozen=True, slots=True)
class ConsumerSettings:
    stream: str
    group: str
    consumer: str
    max_attempts: int = 5
    visibility_timeout_ms: int = 60_000
    batch_size: int = 4
    block_ms: int = 1_000
    retry_base_delay_s: float = 2.0
    reclaim_every_s: float = 5.0


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _raw_envelope(fields: Fields) -> bytes | str | None:
    return fields.get(ENVELOPE_FIELD.encode()) or fields.get(ENVELOPE_FIELD)


class StreamConsumer:
    def __init__(
        self,
        redis: Redis,
        registry: HandlerRegistry,
        settings: ConsumerSettings,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        self._redis = redis
        self._registry = registry
        self._s = settings
        self._log = log.bind(stream=settings.stream, consumer=settings.consumer)
        visibility_s = settings.visibility_timeout_ms / 1000
        self._heartbeat_s = max(visibility_s / 3, 0.02)
        self._idempotency = IdempotencyGuard(redis, processing_ttl_s=max(60, int(visibility_s * 3)))
        self._progress = ProgressPublisher(redis)
        self._reclaim_cursor = "0-0"

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self._s.stream, self._s.group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def run_once(self) -> int:
        """Reads and processes one batch of new messages. Returns how many were read."""
        response = cast(
            StreamBatch | None,
            await self._redis.xreadgroup(
                self._s.group,
                self._s.consumer,
                {self._s.stream: ">"},
                count=self._s.batch_size,
                block=self._s.block_ms,
            ),
        )
        messages = [m for _, batch in response or [] for m in batch]
        results = await asyncio.gather(
            *(self._process(_text(mid), fields) for mid, fields in messages),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                # Infrastructure failure mid-processing: the message stays pending and is reclaimed.
                self._log.error(
                    "message processing error", error_class="transient", error=redact(str(result))
                )
        return len(messages)

    async def reclaim_stale(self) -> int:
        """Takes over messages left idle by dead consumers (XAUTOCLAIM) and processes them."""
        next_id, claimed, _deleted = await self._redis.xautoclaim(
            self._s.stream,
            self._s.group,
            self._s.consumer,
            min_idle_time=self._s.visibility_timeout_ms,
            start_id=self._reclaim_cursor,
            count=self._s.batch_size,
        )
        self._reclaim_cursor = _text(next_id)  # "0-0" once the whole PEL was scanned
        for message_id, fields in claimed:
            mid = _text(message_id)
            deliveries = await self._delivery_count(mid)
            if deliveries > self._s.max_attempts:
                envelope = self._try_parse(fields)
                reason = f"delivered {deliveries} times without completing"
                await self._dead_letter(
                    mid, fields, ErrorClass.TRANSIENT, reason, envelope=envelope
                )
                continue
            await self._process(mid, fields)
        return len(claimed)

    async def run(self, stop: asyncio.Event) -> None:
        await self.ensure_group()
        last_reclaim = 0.0
        while not stop.is_set():
            try:
                if time.monotonic() - last_reclaim >= self._s.reclaim_every_s:
                    await self.reclaim_stale()
                    last_reclaim = time.monotonic()
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except ResponseError as exc:
                if "NOGROUP" in str(exc):  # stream or group deleted underneath us
                    await self.ensure_group()
                    continue
                self._log.exception("consumer loop error", error_class="transient")
                await self._pause(stop)
            except Exception:  # keep the loop alive; the error is logged with context
                self._log.exception("consumer loop error", error_class="transient")
                await self._pause(stop)

    @staticmethod
    async def _pause(stop: asyncio.Event) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1.0)

    async def _delivery_count(self, message_id: str) -> int:
        entries = await self._redis.xpending_range(
            self._s.stream, self._s.group, min=message_id, max=message_id, count=1
        )
        return int(entries[0]["times_delivered"]) if entries else 0

    @staticmethod
    def _try_parse(fields: Fields) -> JobEnvelope | None:
        raw = _raw_envelope(fields)
        if raw is None:
            return None
        try:
            return parse_envelope(raw)
        except InvalidInputError:
            return None

    async def _process(self, message_id: str, fields: Fields) -> None:
        raw = _raw_envelope(fields)
        if raw is None:
            await self._dead_letter(message_id, fields, ErrorClass.INVALID_INPUT, "no envelope")
            return
        try:
            envelope = parse_envelope(raw)
        except InvalidInputError as exc:
            await self._dead_letter(message_id, fields, ErrorClass.INVALID_INPUT, str(exc))
            return

        org = None if envelope.org_id is None else str(envelope.org_id)
        log = self._log.bind(
            job_id=str(envelope.job_id),
            job_type=envelope.type,
            trace_id=envelope.trace_id,
            org_id=org,
            attempt=envelope.attempt,
            message_id=message_id,
        )
        key = idempotency_key(org, envelope.idempotency_key)
        claim = await self._idempotency.claim(key, message_id)
        if claim != "acquired":
            # "done": already completed. "busy": another message of the same job is running and
            # owns it (a crash there is recovered by reclaiming that message). Drop this one.
            log.info("duplicate delivery acknowledged", claim=claim)
            await self._ack(message_id)
            return

        handler = self._registry.get(envelope.type)
        if handler is None:
            await self._idempotency.release(key, message_id)
            reason = f"no handler for job type {envelope.type}"
            await self._dead_letter(
                message_id,
                fields,
                ErrorClass.INVALID_INPUT,
                reason,
                attempts=envelope.attempt,
                envelope=envelope,
            )
            return

        ctx = JobContext(
            envelope=envelope,
            redis=self._redis,
            progress=self._progress,
            log=log,
            message_id=message_id,
        )
        heartbeat = asyncio.create_task(self._heartbeat(key, message_id))
        try:
            with _tracer.start_as_current_span(f"job {envelope.type}") as span:
                span.set_attribute("leadforge.job_id", str(envelope.job_id))
                span.set_attribute("leadforge.attempt", envelope.attempt)
                await handler(ctx)
        except asyncio.CancelledError:
            heartbeat.cancel()
            await self._idempotency.release(key, message_id)
            raise
        except Exception as exc:  # classified below; nothing is swallowed
            heartbeat.cancel()
            await self._idempotency.release(key, message_id)
            await self._on_failure(ctx, fields, exc)
            return
        heartbeat.cancel()
        await self._idempotency.complete(key)
        await self._ack(message_id)
        log.info("job completed")

    async def _heartbeat(self, key: str, message_id: str) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_s)
            with contextlib.suppress(Exception):  # a missed beat only risks an early reclaim
                await self._redis.xclaim(
                    self._s.stream,
                    self._s.group,
                    self._s.consumer,
                    min_idle_time=0,
                    message_ids=[message_id],
                    justid=True,
                )
                await self._idempotency.heartbeat(key, message_id)

    async def _on_failure(self, ctx: JobContext, fields: Fields, exc: Exception) -> None:
        envelope = ctx.envelope
        error_class = classify(exc)
        error = redact(str(exc))
        ctx.log.warning("job failed", error_class=error_class.value, error=error)

        if error_class in RETRYABLE and envelope.attempt < self._s.max_attempts:
            retry = envelope.model_copy(update={"attempt": envelope.attempt + 1})
            delay = backoff_seconds(
                envelope.attempt,
                getattr(exc, "retry_after_s", None),
                base_s=self._s.retry_base_delay_s,
            )
            async with self._redis.pipeline(transaction=True) as pipe:
                queue_retry(pipe, self._s.stream, retry.to_wire(), delay)
                pipe.xack(self._s.stream, self._s.group, ctx.message_id)
                await pipe.execute()
            ctx.log.info("job retry scheduled", next_attempt=retry.attempt, delay_s=round(delay, 2))
            return

        if error_class is ErrorClass.BUDGET_EXHAUSTED:
            # Persist first, then announce (same order as the DLQ path).
            await self._notify_failure(envelope, error_class, error, envelope.attempt)
            await self._final_progress(envelope, "paused", error_class, error)
            await self._ack(ctx.message_id)
            return
        await self._dead_letter(
            ctx.message_id,
            fields,
            error_class,
            error,
            attempts=envelope.attempt,
            envelope=envelope,
        )

    async def _final_progress(
        self, envelope: JobEnvelope, status: Status, error_class: ErrorClass, message: str
    ) -> None:
        with contextlib.suppress(Exception):  # best effort; the DLQ entry / ack is what counts
            await self._progress.publish(
                job_id=str(envelope.job_id),
                org_id=None if envelope.org_id is None else str(envelope.org_id),
                trace_id=envelope.trace_id,
                stage=self._registry.stage_for(envelope.type),
                status=status,
                message=message,
                error_class=error_class.value,
            )

    async def _dead_letter(
        self,
        message_id: str,
        fields: Fields,
        error_class: ErrorClass,
        error: str,
        *,
        attempts: int | None = None,
        envelope: JobEnvelope | None = None,
    ) -> None:
        error = redact(error)
        if envelope is not None:
            await self._notify_failure(envelope, error_class, error, attempts)
            await self._final_progress(envelope, "failed", error_class, error)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.xadd(
                dlq_for(self._s.stream),
                {
                    ENVELOPE_FIELD: _raw_envelope(fields) or b"",
                    "error_class": error_class.value,
                    "error": error,
                    "attempts": "" if attempts is None else str(attempts),
                    "source_message_id": message_id,
                    "failed_at": datetime.now(UTC).isoformat(),
                },
                maxlen=STREAM_MAXLEN,
                approximate=True,
            )
            pipe.xack(self._s.stream, self._s.group, message_id)
            await pipe.execute()
        self._log.error(
            "job dead-lettered", message_id=message_id, error_class=error_class.value, error=error
        )

    async def _notify_failure(
        self, envelope: JobEnvelope, error_class: ErrorClass, error: str, attempts: int | None
    ) -> None:
        """Lets the handler module persist the failure (e.g. job_runs.status). Never raises."""
        hook = self._registry.failure_hook(envelope.type)
        if hook is None:
            return
        try:
            await hook(envelope, error_class, error, attempts)
        except Exception:
            self._log.exception(
                "failure hook error", job_id=str(envelope.job_id), error_class="transient"
            )

    async def _ack(self, message_id: str) -> None:
        await self._redis.xack(self._s.stream, self._s.group, message_id)
