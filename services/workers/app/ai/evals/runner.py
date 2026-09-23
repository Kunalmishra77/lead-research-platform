"""Running an eval set against a task (docs/07 evals).

Two modes, on purpose:

* **live** calls the model. This is what you run before changing a prompt or a model, and what
  produces the numbers in `RESULTS.md`.
* **replay** reads recorded answers from disk. This is what CI runs — a smoke subset that proves
  the prompt, the schema, the scorer and the spec builder still fit together, without spending
  money or depending on a provider being up (docs/13: tests never hit live third-party APIs).

Recording is a side effect of a live run (`--record`), so a replay set is never hand-written.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app.ai.config import model_for, price_for, task_config
from app.ai.evals.scorers import SCORERS, CaseScore
from app.ai.grounding import new_fence, render_user_message, safety_preamble
from app.ai.prompt_registry import get_prompt
from app.ai.providers.base import ModelProvider
from app.ai.schemas import get_schema
from app.config import Settings

CASE_ROOT = Path(__file__).resolve().parent / "cases"
RECORDING_ROOT = Path(__file__).resolve().parent / "recordings"

#: Model calls run in parallel; enough to be quick, few enough to stay inside a rate limit.
DEFAULT_CONCURRENCY = 6


@dataclass(frozen=True, slots=True)
class EvalCase:
    id: str
    input: dict[str, Any]
    expected: dict[str, Any]
    tags: tuple[str, ...] = ()

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "EvalCase":
        return cls(
            id=raw["id"],
            input=raw["input"],
            expected=raw["expected"],
            tags=tuple(raw.get("tags", [])),
        )


@dataclass
class EvalReport:
    task: str
    model: str
    prompt_version: str
    scores: list[CaseScore] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    duration_s: float = 0.0
    #: What this run cost us. Zero on a replay, which calls nothing.
    cost_micros: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_micros_per_1k(self) -> int:
        """Cost per 1000 cases, which is the number docs/07 asks RESULTS.md to carry."""
        return round(self.cost_micros * 1000 / self.total) if self.total else 0

    @property
    def total(self) -> int:
        return len(self.scores) + len(self.errors)

    @property
    def passed(self) -> int:
        return sum(1 for s in self.scores if s.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def field_accuracy(self) -> float:
        checked = sum(s.checked for s in self.scores)
        matched = sum(s.matched for s in self.scores)
        return matched / checked if checked else 0.0

    def failures(self) -> list[CaseScore]:
        return [s for s in self.scores if not s.passed]

    def summary(self) -> str:
        lines = [
            f"task={self.task} model={self.model} prompt={self.prompt_version}",
            f"cases={self.total} passed={self.passed} "
            f"pass_rate={self.pass_rate:.1%} field_accuracy={self.field_accuracy:.1%} "
            f"errors={len(self.errors)} in {self.duration_s:.1f}s",
            f"cost={self.cost_micros} micros "
            f"({self.cost_micros_per_1k} per 1k cases, "
            f"{self.input_tokens} in / {self.output_tokens} out)",
        ]
        for score in self.failures():
            for name, (want, got) in score.diffs.items():
                lines.append(f"  FAIL {score.case_id}: {name}: expected {want!r}, got {got!r}")
        for case_id, message in self.errors:
            lines.append(f"  ERROR {case_id}: {message}")
        return "\n".join(lines)


def load_cases(task: str, root: Path | None = None) -> list[EvalCase]:
    path = (root or CASE_ROOT) / f"{task}.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"no eval cases for {task}: {path}")
    cases = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("//"):
            cases.append(EvalCase.from_json(json.loads(line)))
    if len({c.id for c in cases}) != len(cases):
        raise ValueError(f"duplicate case ids in {path}")
    return cases


@dataclass(frozen=True, slots=True)
class Recording:
    """Answers from one live run, with what produced them.

    The metadata is the point: without it a replay would keep passing after a prompt or model
    change, which is exactly the change you most want a replay to notice.
    """

    meta: dict[str, Any]
    answers: dict[str, Any]

    @property
    def model(self) -> str:
        return str(self.meta.get("model", ""))

    @property
    def prompt_version(self) -> str:
        return str(self.meta.get("prompt_version", ""))


def load_recording(task: str, root: Path | None = None) -> Recording:
    path = (root or RECORDING_ROOT) / f"{task}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no recorded answers for {task}: {path}")
    stored = json.loads(path.read_text(encoding="utf-8"))
    return Recording(meta=stored.get("meta", {}), answers=stored.get("answers", {}))


def save_recording(task: str, recording: Recording, root: Path | None = None) -> Path:
    directory = root or RECORDING_ROOT
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{task}.json"
    path.write_text(
        json.dumps({"meta": recording.meta, "answers": recording.answers}, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return path


async def run_eval(
    task: str,
    *,
    provider: ModelProvider | None,
    settings: Settings,
    cases: list[EvalCase] | None = None,
    model: str | None = None,
    prompt_version: int | None = None,
    replay: Recording | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> tuple[EvalReport, Recording]:
    """Scores one task over its cases. Returns the report and the answers, ready to record.

    The gateway is deliberately not used: an eval must not be served from the response cache,
    and it must not book credits against a research job that does not exist.
    """
    config = task_config(task)
    prompt = get_prompt(
        task, prompt_version if prompt_version is not None else config.prompt_version
    )
    schema = get_schema(task)
    validator = Draft202012Validator(schema)
    scorer = SCORERS[task]
    chosen_model = model or model_for(config.tier, settings)
    cases = cases if cases is not None else load_cases(task)

    report = EvalReport(task=task, model=chosen_model, prompt_version=prompt.ref)
    answers: dict[str, Any] = {}
    semaphore = asyncio.Semaphore(concurrency)
    started = time.monotonic()

    async def answer_for(case: EvalCase) -> dict[str, Any]:
        if replay is not None:
            recorded = replay.answers.get(case.id)
            if recorded is None:
                raise KeyError(f"case {case.id} is not in the recording")
            return dict(recorded)
        if provider is None:
            raise ValueError("a live run needs a provider")
        async with semaphore:
            fence = new_fence()
            result = await provider.complete_json(
                model=chosen_model,
                system=f"{safety_preamble(fence)}\n\n{prompt.system}".strip(),
                messages=[
                    {"role": "user", "content": render_user_message(prompt, case.input, fence)}
                ],
                schema=schema,
                schema_name=task,
                max_output_tokens=config.max_output_tokens,
                timeout_s=settings.AI_TIMEOUT_S,
                reasoning_effort=config.reasoning_effort,
            )
        # Charged whatever came back, so the run's cost is the real one (CLAUDE.md, ADR-0010).
        report.input_tokens += result.usage.input_tokens + result.usage.cache_read_tokens
        report.output_tokens += result.usage.output_tokens
        report.cost_micros += price_for(result.model or chosen_model).cost_micros(
            result.usage.input_tokens,
            result.usage.output_tokens,
            result.usage.cache_read_tokens,
            result.usage.cache_write_tokens,
        )
        if result.failure:
            raise ValueError(result.failure)
        return result.data

    async def one(case: EvalCase) -> None:
        try:
            data = await answer_for(case)
            validator.validate(data)
            score = scorer(case.expected, data)
        except Exception as exc:  # one bad case must not lose the other 49
            report.errors.append((case.id, f"{type(exc).__name__}: {exc}"))
            return
        answers[case.id] = data
        score.case_id = case.id
        report.scores.append(score)

    await asyncio.gather(*(one(case) for case in cases))
    report.scores.sort(key=lambda s: s.case_id)
    report.errors.sort()
    report.duration_s = time.monotonic() - started

    recording = Recording(
        meta={
            "model": chosen_model,
            "prompt_version": prompt.ref,
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "cases": len(cases),
            "cost_micros": report.cost_micros,
            # The score this baseline was taken at. A replay must reproduce it exactly: a
            # recording with a known failure is still a valid regression fixture, as long as
            # nothing pretends it was perfect.
            "pass_rate": round(report.pass_rate, 4),
            "field_accuracy": round(report.field_accuracy, 4),
        },
        answers=answers,
    )
    return report, recording
