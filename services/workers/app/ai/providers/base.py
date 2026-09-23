"""What the gateway needs from any model provider (docs/07: adapters, one gateway).

Structured output is the only mode we use: the caller hands over a JSON Schema and gets a dict
back. Nothing above this layer knows whether the provider implements that with tool use, a
response-format flag or something else.
"""

from typing import Any, Protocol

from app.ai.types import ProviderResult, ReasoningEffort


class ModelProvider(Protocol):
    name: str

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
        """Runs one call and returns the structured payload the schema describes.

        Raises a classified `JobError`: `TransientError` for anything worth another attempt,
        `RateLimitedError` with `retry_after_s` where the provider says so, `InvalidInputError`
        for a request the provider rejected. A response that arrived but is unusable is returned
        with `failure` set, never raised — its tokens were billed and must be recorded.
        """
        ...

    async def aclose(self) -> None: ...
