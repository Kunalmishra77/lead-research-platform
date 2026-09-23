"""The one way to call a model (docs/07).

Everything a model call needs to be safe and accountable lives here, so no other module has to
remember any of it: routing by task, prompt versioning, provider-native JSON schema, validation
with a single repair turn, a response cache, a fallback model, the per-job cost cap, and a
`usage_events` row for every call we paid for — including the calls that produced nothing.
"""

import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import structlog
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError
from redis.asyncio import Redis

from app.ai.cache import AiResponseCache, cache_key
from app.ai.config import FALLBACK_TIER, TaskConfig, model_for
from app.ai.grounding import SAFETY_RULES, new_fence, render_user_message, safety_preamble
from app.ai.prompt_registry import get_prompt
from app.ai.providers.base import ModelProvider
from app.ai.schemas import get_schema
from app.ai.spend import (
    AI_METER,
    Accrual,
    JobSpend,
    call_unit_key,
    estimate_micros,
    record_usage,
    settle_failure,
)
from app.ai.tasks import resolve_task
from app.ai.types import AiResult, Origin, Prompt, ProviderResult
from app.config import Settings
from app.jobs.errors import InvalidInputError, ParseFailedError, TransientError
from app.metering.context import CallContext
from app.metering.usage import NullUsageRecorder, UsageRecorder

__all__ = ["AI_METER", "SAFETY_RULES", "AiGateway"]


class AiGateway:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        redis: Redis,
        settings: Settings,
        usage: UsageRecorder | None = None,
        log: structlog.stdlib.BoundLogger | None = None,
        prompts: Callable[[str, int | None], Prompt] = get_prompt,
        schemas: Callable[[str], dict[str, Any]] = get_schema,
    ) -> None:
        self._prompts = prompts
        self._schemas = schemas
        self._provider = provider
        self._redis = redis
        self._settings = settings
        self._usage = usage or NullUsageRecorder()
        self._cache = AiResponseCache(redis, enabled=settings.AI_CACHE_ENABLED)
        self._log = log or structlog.get_logger("leadforge.ai")

    async def run(
        self,
        task: str,
        payload: dict[str, Any],
        ctx: CallContext,
        *,
        prompt_version: int | None = None,
    ) -> AiResult:
        """Runs one task and returns output that has been validated against the task's schema."""
        if ctx.research_job_id is None:
            # Refused before anything is spent: a model call is never free (docs/11).
            raise InvalidInputError(f"ai task {task} has no research job to meter against")

        config, prompt, schema = resolve_task(
            task, prompt_version, prompts=self._prompts, schemas=self._schemas
        )
        model = model_for(config.tier, self._settings)
        log = self._log.bind(
            task=task,
            model=model,
            prompt_version=prompt.ref,
            org_id=ctx.org_id,
            job_id=ctx.research_job_id,
            trace_id=ctx.trace_id,
        )

        key = cache_key(
            org_id=ctx.org_id,
            task=task,
            prompt_version=prompt.ref,
            model=model,
            max_output_tokens=config.max_output_tokens,
            payload=payload,
        )
        cached = await self._cache.get(key)
        if cached is not None:
            log.info("ai cache hit")
            return cached

        started = time.monotonic()
        accrual = Accrual()
        spend = JobSpend(self._redis, ctx)
        try:
            result = await self._call_with_fallback(
                task=task,
                config=config,
                model=model,
                prompt=prompt,
                payload=payload,
                schema=schema,
                accrual=accrual,
                spend=spend,
                log=log,
            )
            result = replace(result, latency_ms=int((time.monotonic() - started) * 1000))
            # Metered before caching: a failure to record must not leave a free answer behind.
            await record_usage(
                self._usage, ctx, unit_key=call_unit_key(key), accrual=accrual, log=log
            )
            await self._cache.set(key, result, config.cache_ttl_s)
        except BaseException:
            # Cancellation included: a job cancelled mid-call has still been billed, and its
            # reservation must not stay claimed against the cap (ADR-0008).
            await settle_failure(
                self._usage, ctx, spend=spend, accrual=accrual, cache_key=key, log=log
            )
            raise
        log.info(
            "ai call",
            origin=result.origin,
            cost_micros=result.cost_micros,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cached_tokens=result.usage.cache_read_tokens,
            latency_ms=result.latency_ms,
        )
        return result

    async def _call_with_fallback(
        self,
        *,
        task: str,
        config: TaskConfig,
        model: str,
        prompt: Prompt,
        payload: dict[str, Any],
        schema: dict[str, Any],
        accrual: Accrual,
        spend: JobSpend,
        log: structlog.stdlib.BoundLogger,
    ) -> AiResult:
        try:
            return await self._call(
                task=task,
                config=config,
                model=model,
                prompt=prompt,
                payload=payload,
                schema=schema,
                accrual=accrual,
                spend=spend,
                log=log,
            )
        except TransientError as exc:
            # Only an unavailable model is worth another tier. A rate limit is not: the quota is
            # ours, a bigger model costs more, and the job runner already waits out Retry-After.
            fallback_tier = FALLBACK_TIER[config.tier]
            if fallback_tier is None:
                raise
            fallback = model_for(fallback_tier, self._settings)
            if fallback == model:
                raise
            log.warning("ai falling back", fallback_model=fallback, reason=str(exc))
            return await self._call(
                task=task,
                config=config,
                model=fallback,
                prompt=prompt,
                payload=payload,
                schema=schema,
                accrual=accrual,
                spend=spend,
                log=log.bind(model=fallback),
            )

    async def _call(
        self,
        *,
        task: str,
        config: TaskConfig,
        model: str,
        prompt: Prompt,
        payload: dict[str, Any],
        schema: dict[str, Any],
        accrual: Accrual,
        spend: JobSpend,
        log: structlog.stdlib.BoundLogger,
    ) -> AiResult:
        fence = new_fence()
        system = f"{safety_preamble(fence)}\n\n{prompt.system}".strip()
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": render_user_message(prompt, payload, fence)}
        ]
        origin: Origin = "model"
        problem = ""

        for attempt in range(config.repair_attempts + 1):
            provider_result = await self._attempt(
                system=system,
                messages=messages,
                schema=schema,
                task=task,
                config=config,
                model=model,
                accrual=accrual,
                spend=spend,
            )
            if provider_result.failure:
                raise ParseFailedError(f"{task}: {provider_result.failure}")

            problem = _validate(provider_result, schema)
            if not problem:
                return AiResult(
                    task=task,
                    data=provider_result.data,
                    model=provider_result.model or model,
                    prompt_version=prompt.ref,
                    origin=origin,
                    usage=accrual.usage,
                    cost_micros=accrual.cost_micros,
                    observed_at=datetime.now(UTC),
                )
            if attempt >= config.repair_attempts:
                break
            log.warning("ai output invalid, repairing", problem=problem, attempt=attempt + 1)
            messages = [
                *messages,
                {"role": "assistant", "content": provider_result.text or "{}"},
                {"role": "user", "content": _repair_instruction(problem)},
            ]
            origin = "repair"

        # One repair turn was already spent; a second usually fails the same way (docs/07).
        raise ParseFailedError(f"{task} output did not match its schema: {problem}")

    async def _attempt(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        task: str,
        config: TaskConfig,
        model: str,
        accrual: Accrual,
        spend: JobSpend,
    ) -> ProviderResult:
        """One provider call, with its cost claimed against the job's cap before it is made."""
        prompt_chars = len(system) + sum(len(str(m.get("content", ""))) for m in messages)
        reserved = estimate_micros(model, prompt_chars, config.max_output_tokens)
        await spend.reserve(reserved, accrual)
        try:
            provider_result = await self._provider.complete_json(
                model=model,
                system=system,
                messages=messages,
                schema=schema,
                schema_name=task,
                max_output_tokens=config.max_output_tokens,
                timeout_s=self._settings.AI_TIMEOUT_S,
                reasoning_effort=config.reasoning_effort,
            )
        except BaseException:
            # The call never landed, so its reservation is not owed. Cancellation included.
            await spend.release(reserved, accrual)
            raise
        accrual.add(provider_result)
        # Reconciled per attempt, not per run: holding a whole output budget across a repair turn
        # would pause jobs at a fraction of their real cap.
        await spend.settle(accrual)
        return provider_result


def _validate(result: ProviderResult, schema: dict[str, Any]) -> str:
    """Returns an empty string when the payload is valid, or why it is not."""
    if not result.data:
        return "the response was not a JSON object"
    try:
        Draft202012Validator(schema).validate(result.data)
    except SchemaValidationError as exc:
        path = "/".join(str(part) for part in exc.absolute_path) or "(root)"
        return f"{path}: {exc.message}"
    return ""


def _repair_instruction(problem: str) -> str:
    return (
        "Your previous answer did not match the required JSON schema.\n"
        f"Problem: {problem}\n"
        "Reply with corrected JSON only. Do not add fields that the schema does not define, "
        "and do not invent values that were not in the input."
    )
