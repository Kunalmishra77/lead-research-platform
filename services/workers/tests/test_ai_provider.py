"""The OpenAI adapter, at the wire level (docs/07, ADR-0009).

Fixtures are recorded from the real Responses API and replayed through the SDK's own HTTP stack,
so these tests pin the actual response shape without ever calling the provider.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from app.ai.providers.openai_provider import OpenAIProvider
from app.jobs.errors import InvalidInputError, RateLimitedError, TransientError
from tests.fixtures import load_json

MODEL = "gpt-5.4-mini-2026-03-17"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "confidence"],
    "properties": {
        "intent": {"type": "string", "enum": ["local_business", "company_list", "people", "other"]},
        "confidence": {"type": "number"},
    },
}

Handler = Callable[[httpx2.Request], httpx2.Response]


def provider_with(handler: Handler) -> OpenAIProvider:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return OpenAIProvider(api_key="sk-test-not-a-real-key", http_client=client)


def responds(case: str, status: int = 200) -> tuple[Handler, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(status, json=load_json("openai", case))

    return handler, seen


async def call(provider: OpenAIProvider, *, max_output_tokens: int = 2000) -> Any:
    return await provider.complete_json(
        model=MODEL,
        system="You classify lead-research requests.",
        messages=[{"role": "user", "content": "Restaurants in Delhi with a website"}],
        schema=SCHEMA,
        schema_name="intent_classify",
        max_output_tokens=max_output_tokens,
        timeout_s=30,
    )


async def test_a_recorded_response_becomes_a_structured_result() -> None:
    handler, seen = responds("intent_classify_success")
    provider = provider_with(handler)
    try:
        result = await call(provider)
    finally:
        await provider.aclose()

    assert result.data == {"intent": "local_business", "confidence": 0.98}
    assert result.model == MODEL
    assert result.usage.input_tokens == 71
    assert result.usage.output_tokens == 21

    sent = json.loads(seen[0].content)
    # Structured output is the provider's own JSON-schema mode, not a hopeful "reply in JSON".
    assert sent["text"]["format"]["type"] == "json_schema"
    assert sent["text"]["format"]["strict"] is True
    assert sent["text"]["format"]["schema"] == SCHEMA
    assert sent["instructions"] == "You classify lead-research requests."
    assert sent["max_output_tokens"] == 2000


async def test_a_refusal_comes_back_with_the_tokens_it_cost() -> None:
    handler, _ = responds("refusal")
    provider = provider_with(handler)
    try:
        result = await call(provider)
    finally:
        await provider.aclose()

    # Raising here would lose the usage: a refusal is billed like any other answer.
    assert result.failure is not None
    assert "declined" in result.failure
    assert result.data == {}
    assert result.usage.output_tokens == 21


async def test_a_truncated_answer_comes_back_with_the_tokens_it_cost() -> None:
    handler, _ = responds("truncated")
    provider = provider_with(handler)
    try:
        result = await call(provider)
    finally:
        await provider.aclose()

    assert result.failure is not None
    assert "max_output_tokens" in result.failure
    assert result.usage.input_tokens + result.usage.output_tokens > 0


async def test_a_server_side_failure_is_reported_rather_than_read_as_empty() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = load_json("openai", "intent_classify_success")
        body["status"] = "failed"
        body["error"] = {"code": "server_error", "message": "something broke"}
        body["output"] = []
        return httpx2.Response(200, json=body)

    provider = provider_with(handler)
    try:
        result = await call(provider)
    finally:
        await provider.aclose()

    assert result.failure is not None
    assert "server_error" in result.failure


async def test_cached_input_tokens_are_not_also_counted_as_fresh_input() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = load_json("openai", "intent_classify_success")
        body["usage"]["input_tokens"] = 100
        body["usage"]["input_tokens_details"]["cached_tokens"] = 60
        return httpx2.Response(200, json=body)

    provider = provider_with(handler)
    try:
        result = await call(provider)
    finally:
        await provider.aclose()

    # `input_tokens` is the total; charging the cached 60 at both rates would overstate cost.
    assert result.usage.input_tokens == 40
    assert result.usage.cache_read_tokens == 60


async def test_the_task_reasoning_effort_reaches_the_request() -> None:
    handler, seen = responds("intent_classify_success")
    provider = provider_with(handler)
    try:
        await provider.complete_json(
            model=MODEL,
            system="s",
            messages=[{"role": "user", "content": "x"}],
            schema=SCHEMA,
            schema_name="intent_classify",
            max_output_tokens=512,
            timeout_s=30,
            reasoning_effort="none",
        )
    finally:
        await provider.aclose()

    # Reasoning tokens share max_output_tokens, so the effort is pinned, not left to a default.
    assert json.loads(seen[0].content)["reasoning"] == {"effort": "none"}


async def test_unparseable_output_is_returned_as_text_for_the_repair_turn() -> None:
    handler, _ = responds("invalid_json")
    provider = provider_with(handler)
    try:
        result = await call(provider)
    finally:
        await provider.aclose()

    # The gateway shows the model its own broken output, so the text has to survive.
    assert result.data == {}
    assert result.text is not None
    assert result.text.startswith('{"intent"')


async def test_a_rate_limit_carries_retry_after_and_is_classified() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            429, headers={"retry-after": "8"}, json=load_json("openai", "rate_limited")
        )

    provider = provider_with(handler)
    try:
        with pytest.raises(RateLimitedError) as err:
            await call(provider)
    finally:
        await provider.aclose()
    assert err.value.retry_after_s == 8.0


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_provider_outages_are_transient(status: int) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json={"error": {"message": "server error"}})

    provider = provider_with(handler)
    try:
        with pytest.raises(TransientError):
            await call(provider)
    finally:
        await provider.aclose()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_a_rejected_request_is_not_retried(status: int) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json={"error": {"message": "bad request"}})

    provider = provider_with(handler)
    try:
        with pytest.raises(InvalidInputError):
            await call(provider)
    finally:
        await provider.aclose()


async def test_the_sdk_does_not_retry_behind_our_back() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(503, json={"error": {"message": "overloaded"}})

    provider = provider_with(handler)
    try:
        with pytest.raises(TransientError):
            await call(provider)
    finally:
        await provider.aclose()

    # Retries belong to the job runner, which can release the message and re-queue with backoff.
    assert calls == 1
