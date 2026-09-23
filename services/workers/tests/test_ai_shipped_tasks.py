"""Every task the gateway advertises must actually be runnable (docs/07).

These run against the shipped `app/ai/prompts/` and `app/ai/schemas/`, not the test fixtures, so
a task can never be configured without the files it needs, and a schema can never be one the
provider will reject at request time.
"""

from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.ai.config import TASKS
from app.ai.prompt_registry import INPUT_PLACEHOLDER, get_prompt
from app.ai.schemas import get_schema

TASK_NAMES = sorted(TASKS)


def test_at_least_one_task_ships() -> None:
    assert TASK_NAMES, "TASKS is empty; the gateway would have nothing to run"


@pytest.mark.parametrize("task", TASK_NAMES)
def test_every_configured_task_has_a_prompt_and_a_schema(task: str) -> None:
    prompt = get_prompt(task, None)
    assert INPUT_PLACEHOLDER in prompt.template
    assert prompt.system.strip()

    schema = get_schema(task)
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("task", TASK_NAMES)
def test_every_shipped_schema_is_accepted_by_strict_structured_output(task: str) -> None:
    """Strict mode rejects a schema at request time, which would be a permanent failure.

    Verified live against the Responses API: `required` must name every property, and every
    object must set `additionalProperties: false`. (`minimum`/`maximum` are accepted.)
    """
    _assert_strict(get_schema(task), path=task)


def _assert_strict(schema: dict[str, Any], *, path: str) -> None:
    """Walks every subschema, not only `properties`, so a nested object cannot slip through."""
    if schema.get("type") == "object":
        properties = schema.get("properties", {})
        assert schema.get("additionalProperties") is False, (
            f"{path}: additionalProperties must be false"
        )
        assert set(schema.get("required", [])) == set(properties), (
            f"{path}: 'required' must name every property"
        )

    for keyword in ("properties", "$defs", "definitions", "patternProperties"):
        for name, subschema in (schema.get(keyword) or {}).items():
            if isinstance(subschema, dict):
                _assert_strict(subschema, path=f"{path}/{keyword}/{name}")

    for keyword in ("items", "contains", "additionalItems", "not"):
        subschema = schema.get(keyword)
        if isinstance(subschema, dict):
            _assert_strict(subschema, path=f"{path}/{keyword}")

    for keyword in ("anyOf", "oneOf", "allOf", "prefixItems"):
        for index, subschema in enumerate(schema.get(keyword) or []):
            if isinstance(subschema, dict):
                _assert_strict(subschema, path=f"{path}/{keyword}[{index}]")


def test_the_strict_mode_check_catches_a_nested_violation() -> None:
    nested = {
        "type": "object",
        "additionalProperties": False,
        "required": ["item"],
        "properties": {
            "item": {
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {"a": {"type": "string"}},
            }
        },
    }
    with pytest.raises(AssertionError, match="must name every property"):
        _assert_strict(nested, path="probe")


@pytest.mark.parametrize("task", TASK_NAMES)
def test_a_tasks_token_budget_leaves_room_for_the_answer(task: str) -> None:
    config = TASKS[task]
    # Reasoning tokens are drawn from the same budget, so "none" plus a small budget is the
    # only safe combination until a task opts into reasoning deliberately.
    assert config.max_output_tokens >= 256
    if config.reasoning_effort != "none":
        assert config.max_output_tokens >= 2048, task
