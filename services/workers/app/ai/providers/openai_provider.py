"""OpenAI adapter — the only module allowed to import a provider SDK (docs/07, ADR-0009).

The SDK ships its own HTTP stack (`httpx2`, not the `httpx` the connectors use), so an injected
client and its test transport come from there too.

Structured output uses the Responses API's native JSON-schema mode, not free text we then hope to
parse. Transport retries are off: a rate limit or an outage must reach the gateway so it can be
classified, logged and metered, instead of being silently absorbed here.

A response that arrived but is unusable — a refusal, a truncated answer, a server-side failure —
is returned as a `ProviderResult` carrying `failure` and the token usage, never raised. Those
tokens were billed, and the gateway cannot record what it never sees.
"""

import json
from typing import Any, cast

import httpx2
import openai
from openai.types.responses import Response

from app.ai.types import ProviderResult, ReasoningEffort, TokenUsage
from app.jobs.errors import InvalidInputError, RateLimitedError, TransientError

#: Provider failures that are worth another attempt later.
_TRANSIENT = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
    openai.ConflictError,
)

#: Failures caused by the request itself; retrying sends the same bad request again.
_INVALID = (
    openai.BadRequestError,
    openai.UnprocessableEntityError,
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.NotFoundError,
)

#: Statuses outside the SDK's named exceptions that are still worth retrying.
_TRANSIENT_STATUS = frozenset({408, 425, 500, 502, 503, 504})


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            http_client=http_client,
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def complete_json(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        schema_name: str,
        max_output_tokens: int,
        timeout_s: float,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> ProviderResult:
        try:
            response = await self._client.responses.create(
                model=model,
                instructions=system or openai.omit,
                input=cast(Any, messages),
                text=cast(
                    Any,
                    {
                        "format": {
                            "type": "json_schema",
                            "name": schema_name,
                            "schema": schema,
                            "strict": True,
                        }
                    },
                ),
                # Reasoning tokens come out of the same budget as the answer, so the task's
                # effort is pinned rather than left to a server default that could change.
                reasoning=cast(Any, {"effort": reasoning_effort})
                if reasoning_effort
                else openai.omit,
                max_output_tokens=max_output_tokens,
                timeout=timeout_s,
            )
        except openai.RateLimitError as exc:
            raise RateLimitedError(
                f"openai rate limited ({model})", retry_after_s=_retry_after(exc)
            ) from exc
        except _TRANSIENT as exc:
            raise TransientError(f"openai unavailable ({model}): {type(exc).__name__}") from exc
        except _INVALID as exc:
            raise InvalidInputError(f"openai rejected the request ({model}): {exc}") from exc
        except openai.APIStatusError as exc:
            # Any status the SDK has no named class for; retry only the ones worth retrying.
            if exc.status_code in _TRANSIENT_STATUS:
                raise TransientError(f"openai http {exc.status_code} ({model})") from exc
            raise InvalidInputError(f"openai http {exc.status_code} ({model}): {exc}") from exc
        except openai.OpenAIError as exc:
            # Nothing from the SDK reaches the job runner unclassified.
            raise TransientError(f"openai call failed ({model}): {type(exc).__name__}") from exc

        return _to_result(response, model)


def _to_result(response: Response, requested_model: str) -> ProviderResult:
    usage = _usage_of(response)
    model = response.model or requested_model

    unusable = _unusable_reason(response)
    if unusable:
        return _failed(model, usage, unusable)

    text = response.output_text.strip()
    try:
        data = json.loads(text)
    except ValueError:
        # Kept as text so the gateway can show the model its own broken output in the repair turn.
        return ProviderResult(data={}, model=model, usage=usage, text=text)
    if not isinstance(data, dict):
        return ProviderResult(data={}, model=model, usage=usage, text=text)
    return ProviderResult(data=data, model=model, usage=usage, text=text)


def _unusable_reason(response: Response) -> str | None:
    """Why this response carries no answer, or None when it does. Tokens were billed regardless."""
    refusal = _refusal(response)
    if refusal:
        return f"the model declined to answer: {refusal}"
    if response.incomplete_details is not None:
        # Truncated JSON is never valid; a bigger token budget for the task is the real fix.
        return f"incomplete ({response.incomplete_details.reason})"
    if response.error is not None:
        return f"provider error ({response.error.code})"
    if response.status not in (None, "completed"):
        return f"response status {response.status}"
    if not response.output_text.strip():
        return "no structured output"
    return None


def _failed(model: str, usage: TokenUsage, reason: str) -> ProviderResult:
    return ProviderResult(data={}, model=model, usage=usage, text=None, failure=reason)


def _usage_of(response: Response) -> TokenUsage:
    if response.usage is None:
        return TokenUsage()
    details = response.usage.input_tokens_details
    cached = getattr(details, "cached_tokens", 0) or 0
    return TokenUsage(
        # `input_tokens` is the total and includes the cached ones, which are billed at a
        # different rate; keeping them separate means never paying for them twice.
        input_tokens=max(response.usage.input_tokens - cached, 0),
        output_tokens=response.usage.output_tokens,
        cache_read_tokens=cached,
        cache_write_tokens=getattr(details, "cache_write_tokens", 0) or 0,
    )


def _refusal(response: Response) -> str | None:
    for item in response.output:
        for block in getattr(item, "content", None) or []:
            if getattr(block, "type", None) == "refusal":
                return str(getattr(block, "refusal", "")) or "refused"
    return None


def _retry_after(exc: openai.RateLimitError) -> float | None:
    raw = exc.response.headers.get("retry-after") if exc.response is not None else None
    if raw is None:
        return None
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return None
