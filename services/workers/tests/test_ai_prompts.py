"""Prompt registry and response-cache keys (docs/07).

A prompt version is part of the cache key and is stored on every value the prompt produced, so
loading the wrong one, or silently accepting a malformed one, would corrupt both.
"""

from pathlib import Path
from typing import Any

import pytest

from app.ai.cache import cache_key
from app.ai.config import DEFAULT_MODELS, TASKS, price_for, task_config
from app.ai.prompt_registry import (
    INPUT_PLACEHOLDER,
    PromptNotFoundError,
    available_versions,
    load_prompt,
)
from tests.ai_support import ORG, PROMPT_ROOT, TASK


def test_the_newest_version_is_used_by_default() -> None:
    assert available_versions(TASK, PROMPT_ROOT) == [1, 2]
    assert load_prompt(TASK, root=PROMPT_ROOT).version == 2


def test_a_version_can_be_pinned() -> None:
    prompt = load_prompt(TASK, 1, root=PROMPT_ROOT)
    assert prompt.version == 1
    assert prompt.ref == f"{TASK}@v1"


def test_a_missing_task_or_version_fails_loudly() -> None:
    with pytest.raises(PromptNotFoundError, match="no prompt files"):
        load_prompt("not_a_task", root=PROMPT_ROOT)
    with pytest.raises(PromptNotFoundError, match="does not exist"):
        load_prompt(TASK, 99, root=PROMPT_ROOT)


def test_the_system_and_user_halves_are_separated() -> None:
    prompt = load_prompt(TASK, 1, root=PROMPT_ROOT)
    assert prompt.system.startswith("You classify lead-research requests")
    assert INPUT_PLACEHOLDER in prompt.template
    assert "# System" not in prompt.template


def test_a_prompt_without_the_input_placeholder_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "broken"
    directory.mkdir()
    (directory / "v1.md").write_text("# System\n\nhi\n\n# User\n\nno placeholder\n", "utf-8")

    # Otherwise the model would be asked to classify nothing at all.
    with pytest.raises(ValueError, match="placeholder"):
        load_prompt("broken", root=tmp_path)


def test_a_prompt_file_without_headings_is_all_user_template(tmp_path: Path) -> None:
    directory = tmp_path / "plain"
    directory.mkdir()
    (directory / "v1.md").write_text(f"Classify: {INPUT_PLACEHOLDER}\n", "utf-8")

    prompt = load_prompt("plain", root=tmp_path)
    assert prompt.system == ""
    assert prompt.template == f"Classify: {INPUT_PLACEHOLDER}"


def test_the_cache_key_is_stable_and_order_independent() -> None:
    def key(**overrides: Any) -> str:
        args: dict[str, Any] = {
            "org_id": ORG,
            "task": TASK,
            "prompt_version": f"{TASK}@v1",
            "model": "m",
            "max_output_tokens": 256,
            "payload": {"a": 1, "b": 2},
        }
        args.update(overrides)
        return cache_key(**args)

    assert key(payload={"b": 2, "a": 1}) == key()
    assert key().startswith(f"ai:cache:{ORG}:{TASK}:")

    # Anything that can change the answer has to change the key.
    assert key() != key(prompt_version=f"{TASK}@v2")
    assert key() != key(model="other")
    assert key() != key(task="other_task")
    assert key() != key(max_output_tokens=1024)
    assert key() != key(org_id="33333333-3333-7333-8333-333333333333")


def test_every_configured_task_routes_to_a_priced_model() -> None:
    for task, config in TASKS.items():
        assert config.max_output_tokens > 0, task
        model = DEFAULT_MODELS[config.tier]
        price = price_for(model)
        # An unpriced model would report as free and slip past the per-job cost cap.
        assert price.input_micros > 0 and price.output_micros > 0, model


def test_cost_is_rounded_up_so_a_call_is_never_free() -> None:
    price = price_for("gpt-5.4-mini-2026-03-17")
    assert price.cost_micros(1, 0) >= 1
    assert price.cost_micros(1000, 100) == 450
    # Cached input is cheaper but still counted.
    assert price.cost_micros(0, 0, cache_read_tokens=1000) == 25


def test_an_unknown_task_has_no_config() -> None:
    with pytest.raises(KeyError, match="unknown ai task"):
        task_config("not_a_task")
