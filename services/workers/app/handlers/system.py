"""system.* handlers. `system.ping` exercises the whole job path (task 1.10 round trip)."""

import asyncio

from leadforge_contracts.job_envelope import JobEnvelope
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.db.job_runs import JobRunsRepo
from app.jobs.context import JobContext
from app.jobs.errors import ErrorClass, InvalidInputError, TransientError
from app.jobs.registry import HandlerRegistry


class PingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(default="ping", max_length=200)
    #: Total run time, spread over `steps` progress events.
    delay_ms: int = Field(default=0, ge=0, le=60_000)
    steps: int = Field(default=3, ge=1, le=20)
    #: Fail (transiently) while attempt <= fail_times: exercises retries and the DLQ.
    fail_times: int = Field(default=0, ge=0, le=5)


def register_system_handlers(registry: HandlerRegistry, job_runs: JobRunsRepo) -> None:
    async def on_ping_failure(
        envelope: JobEnvelope, error_class: ErrorClass, error: str, attempts: int | None
    ) -> None:
        if envelope.org_id is not None:
            await job_runs.mark_failed(
                str(envelope.org_id), str(envelope.job_id), error_class.value, error, attempts
            )

    @registry.register("system.ping", stage="ping", on_failure=on_ping_failure)
    async def ping(ctx: JobContext) -> None:
        try:
            payload = PingPayload.model_validate(ctx.envelope.payload)
        except ValidationError as exc:
            raise InvalidInputError(f"invalid ping payload: {exc.error_count()} error(s)") from exc
        org = ctx.org_id
        if org is None:
            raise InvalidInputError("system.ping needs an org_id")
        attempt = ctx.envelope.attempt

        async def progress(status: str, step: int, message: str | None = None) -> None:
            await ctx.progress.publish(
                job_id=ctx.job_id,
                org_id=org,
                trace_id=ctx.envelope.trace_id,
                stage="ping",
                status=status,  # type: ignore[arg-type]
                counts={"step": step, "steps": payload.steps, "attempt": attempt},
                message=message,
            )

        if not await job_runs.mark_running(org, ctx.job_id, attempt):
            # Row missing or already finished (e.g. redelivery after completion): nothing to do.
            ctx.log.warning("job run not open; skipping")
            return
        await progress("running", 0, f"attempt {attempt}")
        if attempt <= payload.fail_times:
            raise TransientError(f"simulated failure on attempt {attempt}")
        for step in range(1, payload.steps + 1):
            await asyncio.sleep(payload.delay_ms / payload.steps / 1000)
            await progress("running", step)
        if not await job_runs.mark_completed(
            org, ctx.job_id, {"echo": payload.message, "attempts": attempt}
        ):
            ctx.log.warning("job run finished elsewhere before completion was recorded")
        await progress("completed", payload.steps, payload.message)
