"""Prompt registry: loads `app/ai/prompts/<task>/v<N>.md` (docs/07).

Prompts are versioned files, never string literals at a call site, because the version is part of
the cache key and is stored on every value the prompt produced. A prompt file is markdown with an
optional `# System` / `# User` split; everything before the first heading is the system prompt.
"""

import re
from functools import lru_cache
from pathlib import Path

from app.ai.types import Prompt

PROMPT_ROOT = Path(__file__).resolve().parent / "prompts"

#: Where the caller's payload is spliced into the user template.
INPUT_PLACEHOLDER = "{{input}}"

_VERSION_FILE = re.compile(r"^v(\d+)\.md$")
_SECTION = re.compile(r"^#\s*(system|user)\s*$", re.IGNORECASE | re.MULTILINE)


class PromptNotFoundError(LookupError):
    pass


def available_versions(task: str, root: Path | None = None) -> list[int]:
    directory = (root or PROMPT_ROOT) / task
    if not directory.is_dir():
        return []
    versions = []
    for path in directory.iterdir():
        match = _VERSION_FILE.match(path.name)
        if match:
            versions.append(int(match.group(1)))
    return sorted(versions)


def load_prompt(task: str, version: int | None = None, root: Path | None = None) -> Prompt:
    """Loads one prompt version, or the newest when no version is pinned."""
    versions = available_versions(task, root)
    if not versions:
        raise PromptNotFoundError(f"no prompt files for task {task}")
    chosen = versions[-1] if version is None else version
    if chosen not in versions:
        raise PromptNotFoundError(f"prompt {task}@v{chosen} does not exist")
    path = (root or PROMPT_ROOT) / task / f"v{chosen}.md"
    system, template = _split(path.read_text(encoding="utf-8"))
    if INPUT_PLACEHOLDER not in template:
        raise ValueError(f"prompt {task}@v{chosen} has no {INPUT_PLACEHOLDER} placeholder")
    return Prompt(task=task, version=chosen, system=system, template=template)


@lru_cache(maxsize=64)
def get_prompt(task: str, version: int | None = None) -> Prompt:
    """Cached loader for the shipped prompts; tests that pass a `root` use `load_prompt`."""
    return load_prompt(task, version)


def _split(text: str) -> tuple[str, str]:
    """Returns (system, user template). A file without headings is all user template."""
    sections: dict[str, str] = {}
    matches = list(_SECTION.finditer(text))
    if not matches:
        return "", text.strip()
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1).lower()] = text[match.end() : end].strip()
    return sections.get("system", ""), sections.get("user", "")
