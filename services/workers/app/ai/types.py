"""Shared AI-layer types (docs/07)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

#: Model tiers; the task catalogue in docs/07 assigns one to every task.
Tier = Literal["small", "medium", "large"]

#: Why a result is what it is, for the log and for provenance on AI-derived values.
Origin = Literal["model", "cache", "repair"]

#: How much the model may think before answering. Verified against the provider (ADR-0009).
ReasoningEffort = Literal["none", "low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class Prompt:
    """One version of one task's prompt (`app/ai/prompts/<task>/v<N>.md`)."""

    task: str
    version: int
    system: str
    #: Rendered with the call's input; `{input}` is where the payload goes.
    template: str

    @property
    def ref(self) -> str:
        """What gets stored as `prompt_version` on every value this prompt produced."""
        return f"{self.task}@v{self.version}"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    #: Tokens served from the provider's prompt cache; billed at a lower rate.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """One provider response, already reduced to the structured payload we asked for.

    A call that produced nothing usable still comes back as a result, never as a bare exception:
    the tokens were billed either way, and the gateway has to record them before it fails.
    """

    data: dict[str, Any]
    model: str
    usage: TokenUsage
    #: Raw text when the answer was not the object we asked for; shown back in the repair turn.
    text: str | None = None
    #: Why this call yielded nothing (refusal, truncation, provider error). None when it worked.
    failure: str | None = None


@dataclass(frozen=True, slots=True)
class AiResult:
    """What the gateway hands back: a validated payload and what it cost to get it."""

    task: str
    data: dict[str, Any]
    model: str
    prompt_version: str
    origin: Origin
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_micros: int = 0
    latency_ms: int = 0
    #: When the model produced this. Survives the cache, so a cache hit cannot claim to be fresh.
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
