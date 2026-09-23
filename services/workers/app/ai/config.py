"""Task routing, model tiers and prices (docs/07: routing is configuration, not code).

Changing which model runs a task, or what it is allowed to cost, means editing the tables here
— no call site mentions a model name. `app/config.py` stays the only reader of the environment;
the `AI_MODEL_*` settings override a tier's model without a code change.
"""

from dataclasses import dataclass
from typing import Final

from app.ai.types import ReasoningEffort, Tier
from app.config import Settings

#: Defaults per tier (ADR-0009), verified against `client.models.list()` on 2026-09-23.
#: Dated snapshots, never floating aliases: a silent model change would move eval scores and cost.
DEFAULT_MODELS: Final[dict[Tier, str]] = {
    "small": "gpt-5.4-mini-2026-03-17",
    "medium": "gpt-5.5-2026-04-23",
    "large": "gpt-5.5-pro-2026-04-23",
}

#: Used when the primary model errors or is overloaded; None means "do not fall back".
FALLBACK_TIER: Final[dict[Tier, Tier | None]] = {
    "small": "medium",
    "medium": "large",
    "large": None,
}


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Provider list price in micros per token (docs/11 keeps money in integer minor units)."""

    input_micros: float
    output_micros: float
    cache_read_micros: float = 0.0
    cache_write_micros: float = 0.0

    def cost_micros(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> int:
        total = (
            input_tokens * self.input_micros
            + output_tokens * self.output_micros
            + cache_read_tokens * self.cache_read_micros
            + cache_write_tokens * self.cache_write_micros
        )
        # Round up: undercharging ourselves would let a job slip past its cost cap.
        return int(-(-total // 1))


#: Micros per token. 1 micro = 1e-6 USD, so $3 per million input tokens = 3.0 micros per token.
#: These drive `usage_events.cost_micros` only (never what a customer is charged, docs/11), but
#: they still have to be checked against the provider's current price list before we trust a
#: cost report or a cost cap. Unverified against the provider's price list — see the open
#: item in PROGRESS.md (task 2.2).
PRICES: Final[dict[str, ModelPrice]] = {
    "gpt-5.4-mini-2026-03-17": ModelPrice(
        input_micros=0.25, output_micros=2.0, cache_read_micros=0.025
    ),
    "gpt-5.5-2026-04-23": ModelPrice(
        input_micros=1.25, output_micros=10.0, cache_read_micros=0.125
    ),
    "gpt-5.5-pro-2026-04-23": ModelPrice(input_micros=15.0, output_micros=120.0),
}

#: Charged when a model has no price entry, so an unpriced model can never look free.
UNKNOWN_MODEL_PRICE: Final[ModelPrice] = ModelPrice(input_micros=15.0, output_micros=120.0)


@dataclass(frozen=True, slots=True)
class TaskConfig:
    """Everything that is per task rather than per call (docs/07 task catalogue)."""

    tier: Tier
    max_output_tokens: int
    #: How long an identical call may be answered from cache. 0 disables caching for the task.
    cache_ttl_s: int
    #: A task whose input is user text gets one repair turn; a deterministic one need not.
    repair_attempts: int = 1
    #: Pinned rather than left to a server default: reasoning tokens come out of
    #: `max_output_tokens`, so a change of default could truncate every answer.
    reasoning_effort: ReasoningEffort = "none"
    #: The prompt version in use. Pinned, not "whichever file has the highest number": adding a
    #: prompt must be a deliberate adoption backed by an eval run, never an accident of naming.
    prompt_version: int | None = None


#: A task appears here only once its prompt and schema ship, so `TASKS` can never promise a
#: task the gateway cannot run.
TASKS: Final[dict[str, TaskConfig]] = {
    "intent_classify": TaskConfig(
        tier="small",
        max_output_tokens=1024,
        # An intent does not change for the same words, so this may be cached for a week.
        cache_ttl_s=7 * 24 * 3600,
        prompt_version=5,
    ),
    # The user sees a parse and corrects it before anything is spent, so it is worth a little
    # thinking; the budget covers reasoning tokens as well as the answer.
    # The same trade in the same city expands the same way, and the planner asks once per city
    # per job, so this is the task that benefits most from the cache. A week is safe: the local
    # name for a kind of business does not change faster than a prompt version does.
    "query_expand": TaskConfig(
        tier="small",
        max_output_tokens=1024,
        cache_ttl_s=7 * 24 * 3600,
        prompt_version=1,
    ),
    "spec_parse": TaskConfig(
        tier="small",
        max_output_tokens=4096,
        # Shorter than intent_classify: a parse depends on the field catalogue and the defaults
        # around it, which change more often than the meaning of a sentence does.
        cache_ttl_s=24 * 3600,
        reasoning_effort="medium",
        prompt_version=4,
    ),
}


def task_config(task: str) -> TaskConfig:
    try:
        return TASKS[task]
    except KeyError:
        raise KeyError(f"unknown ai task: {task}") from None


def model_for(tier: Tier, settings: Settings) -> str:
    override = {
        "small": settings.AI_MODEL_SMALL,
        "medium": settings.AI_MODEL_MEDIUM,
        "large": settings.AI_MODEL_LARGE,
    }[tier]
    return override or DEFAULT_MODELS[tier]


def price_for(model: str) -> ModelPrice:
    return PRICES.get(model, UNKNOWN_MODEL_PRICE)
