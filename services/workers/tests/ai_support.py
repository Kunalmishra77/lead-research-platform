"""Shared helpers for the AI gateway tests: a scripted provider and hermetic settings."""

import json
from pathlib import Path
from typing import Any

import structlog

from app.ai.prompt_registry import available_versions, load_prompt
from app.ai.schemas import load_schema
from app.ai.types import Prompt, ProviderResult, TokenUsage
from app.config import Settings
from app.metering.context import CallContext

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "ai"
PROMPT_ROOT = FIXTURE_ROOT / "prompts"
SCHEMA_ROOT = FIXTURE_ROOT / "schemas"

TASK = "intent_classify"
MODEL_SMALL = "gpt-5.4-mini-2026-03-17"
MODEL_MEDIUM = "gpt-5.5-2026-04-23"
ORG = "11111111-1111-7111-8111-111111111111"
JOB = "22222222-2222-7222-8222-222222222222"

VALID: dict[str, Any] = {"intent": "local_business", "confidence": 0.98}


def fixture_prompt(task: str, version: int | None = None) -> Prompt:
    """Fixture prompts, deliberately independent of whichever version production has adopted.

    These tests are about what the gateway does with a prompt, not about which prompt is live,
    so a version the fixtures do not have falls back to the newest fixture rather than failing.
    """
    available = available_versions(task, PROMPT_ROOT)
    if not available:
        raise LookupError(f"no fixture prompts for {task}")
    chosen = version if version in available else available[-1]
    return load_prompt(task, chosen, root=PROMPT_ROOT)


def fixture_schema(task: str) -> dict[str, Any]:
    return load_schema(task, root=SCHEMA_ROOT)


#: Stands in for the envelope's own job id, which is what caps work that has no research job.
ENVELOPE = "33333333-3333-7333-8333-333333333333"


def make_ctx(
    *,
    research_job_id: str | None = JOB,
    cost_cap_micros: int = 0,
    spend_id: str = ENVELOPE,
) -> CallContext:
    return CallContext(
        org_id=ORG,
        research_job_id=research_job_id,
        trace_id="0af7651916cd43dd8448eb211c80319c",
        log=structlog.get_logger("test"),
        cost_cap_micros=cost_cap_micros,
        spend_id=spend_id,
    )


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "NODE_ENV": "test",
        "DATABASE_URL_WORKERS": "postgresql://u:p@localhost:5432/lf",
        "REDIS_URL": "redis://localhost:6379/0",
        "S3_ENDPOINT": "https://s3.example",
        "S3_REGION": "ap-south-1",
        "S3_BUCKET_RAW": "lf-raw",
        "S3_BUCKET_EXPORTS": "lf-exports",
        "S3_ACCESS_KEY_ID": "test",
        "S3_SECRET_ACCESS_KEY": "test",
        "OPENAI_API_KEY": "sk-test-not-a-real-key",
    }
    values.update(overrides)
    # Never read the developer's real .env: these tests must not depend on a machine.
    return Settings(_env_file=None, **values)


class ScriptedProvider:
    """Returns the queued results in order; a queued exception is raised instead."""

    name = "scripted"

    def __init__(self, *script: ProviderResult | BaseException) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def complete_json(self, **kwargs: Any) -> ProviderResult:
        self.calls.append(dict(kwargs))
        if not self.script:
            raise AssertionError(f"unexpected extra call to {kwargs.get('schema_name')}")
        nxt = self.script.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    async def aclose(self) -> None:
        self.closed = True


def answer(
    data: dict[str, Any] | None = None,
    *,
    model: str = MODEL_SMALL,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cache_read_tokens: int = 0,
    text: str | None = None,
    failure: str | None = None,
) -> ProviderResult:
    payload = VALID if data is None else data
    return ProviderResult(
        data=payload,
        model=model,
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
        ),
        text=text if text is not None else json.dumps(payload),
        failure=failure,
    )


def unusable(reason: str, **kwargs: Any) -> ProviderResult:
    """A response that arrived and was billed but carries no answer."""
    return answer({}, text=None, failure=reason, **kwargs)
