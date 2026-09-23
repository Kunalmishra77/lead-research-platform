"""Resolving a task name to everything needed to run it (docs/07).

A task the gateway cannot run is a deployment problem, not a transient one: `classify()` would
otherwise treat a missing prompt file as TRANSIENT and the job runner would re-queue it forever.
"""

from collections.abc import Callable
from typing import Any

from app.ai.config import TaskConfig, task_config
from app.ai.types import Prompt
from app.jobs.errors import InvalidInputError


def resolve_task(
    task: str,
    prompt_version: int | None,
    *,
    prompts: Callable[[str, int | None], Prompt],
    schemas: Callable[[str], dict[str, Any]],
) -> tuple[TaskConfig, Prompt, dict[str, Any]]:
    """Returns the task's config, prompt and output schema, or fails fast."""
    try:
        return task_config(task), prompts(task, prompt_version), schemas(task)
    except (LookupError, ValueError) as exc:
        raise InvalidInputError(f"ai task {task} is not usable: {exc}") from exc
