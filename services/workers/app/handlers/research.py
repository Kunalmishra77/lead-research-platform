"""research.* handlers. `research.parse` answers the parse endpoint (ADR-0005).

The user is waiting, so this replies once whatever happens: a classified error reaches them in a
second, where silence would cost them the caller's full timeout.
"""

from typing import Any

from leadforge_contracts.research_parse_reply import ResearchParseReply
from leadforge_contracts.research_spec import ResearchSpec
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.feasibility import assess
from app.ai.gateway import AiGateway
from app.ai.spec_draft import build_spec, needs_confirmation
from app.error_reporting import report_job_failure
from app.jobs.context import JobContext
from app.jobs.errors import InvalidInputError, ParseFailedError, classify
from app.jobs.redact import redact
from app.jobs.registry import HandlerRegistry
from app.jobs.rpc import check_reply_key, send_failure, send_reply
from app.metering.context import CallContext


class ParsePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_query: str = Field(min_length=1, max_length=2000)
    reply_to: str = Field(min_length=1, max_length=200)


def register_research_handlers(registry: HandlerRegistry, gateway: AiGateway) -> None:
    # A parse publishes no progress (the caller is waiting on a reply, ADR-0005); the stage
    # is only what a failure is logged under, and a parse is the front door to planning.
    @registry.register("research.parse", stage="planning")
    async def parse(ctx: JobContext) -> None:
        try:
            payload = ParsePayload.model_validate(ctx.envelope.payload)
        except ValidationError as exc:
            # Nothing to reply to: without a valid reply key there is nowhere to send this.
            raise InvalidInputError(f"invalid parse payload: {exc.error_count()} error(s)") from exc

        key = check_reply_key(payload.reply_to, ctx.job_id)
        try:
            reply = await _parse(ctx, gateway, payload.raw_query)
        except Exception as exc:
            # Answered, so the job is done even though the parse failed. Re-raising would have
            # the runner retry a request nobody is waiting for any more: the caller already has
            # its error and has stopped listening, so every retry would be a paid call whose
            # answer goes nowhere. A user who wants another attempt asks for one.
            await send_failure(ctx.redis, key, ctx.job_id, exc)
            ctx.log.warning(
                "parse failed; the caller was told",
                error_class=classify(exc).value,
                error=redact(str(exc)),
            )
            # The job is not retried, so nothing downstream would otherwise report this. A
            # parse is the one failure a user watches happen (docs/01 no silent failures).
            report_job_failure(
                exc,
                error_class=classify(exc),
                job_type=ctx.envelope.type,
                job_id=ctx.job_id,
                org_id=ctx.org_id,
                trace_id=ctx.envelope.trace_id,
            )
            return
        await send_reply(ctx.redis, key, reply)


async def _parse(ctx: JobContext, gateway: AiGateway, raw_query: str) -> dict[str, Any]:
    call = CallContext.from_job(ctx)
    result = await gateway.run("spec_parse", {"query": raw_query}, call)
    draft = result.data

    spec = build_spec(draft)
    # Checked against the contract here, not at the caller: a spec the contract rejects is our
    # bug, and the user should see it now rather than when the create endpoint refuses it.
    try:
        ResearchSpec.model_validate(spec)
    except ValidationError as exc:
        raise ParseFailedError(f"built an invalid spec: {exc.error_count()} error(s)") from exc

    unsupported = list(draft.get("unsupported", []))
    feasibility = assess(spec, unsupported)

    reply: dict[str, Any] = {
        "reply_version": 1,
        "job_id": ctx.job_id,
        "ok": True,
        "result": {
            "spec": spec,
            "feasibility": feasibility,
            # The model's own doubt, plus anything the feasibility table says we cannot do:
            # a filter we silently drop is one the user believes is running.
            "needs_confirmation": needs_confirmation(draft)
            or any(row["mode"] == "unsupported" for row in feasibility),
            "ambiguities": list(draft.get("ambiguities", [])),
            "unsupported": unsupported,
            "confidence": float(draft.get("confidence", 0.0)),
            "provenance": {
                "model": result.model,
                "prompt_version": result.prompt_version,
                "observed_at": result.observed_at.isoformat(),
                "cached": result.origin == "cache",
            },
        },
    }
    # The reply crosses a language boundary, so it is checked against the shared contract here.
    # Drift would otherwise be a 502 for every user, discovered in production (ADR-0005).
    try:
        ResearchParseReply.model_validate(reply)
    except ValidationError as exc:
        raise ParseFailedError(f"built an invalid reply: {exc.error_count()} error(s)") from exc
    return reply
