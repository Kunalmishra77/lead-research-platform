"""`uv run python -m app.ai.evals run <task> [--model M] [--replay] [--record]` (docs/07)."""

import argparse
import asyncio
import json
import sys

from app.ai.evals.runner import (
    EvalReport,
    Recording,
    load_cases,
    load_recording,
    run_eval,
    save_recording,
)
from app.ai.providers.openai_provider import OpenAIProvider
from app.config import get_settings

#: A replay is deterministic, so anything less than every case passing is a real regression.
REPLAY_MIN_PASS_RATE = 1.0

#: A live run talks to a model, so the bar is the phase's acceptance criterion, not perfection.
LIVE_MIN_PASS_RATE = 0.9


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.ai.evals")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="score a task's eval set")
    run.add_argument("task")
    run.add_argument("--model", default=None, help="override the task's configured model")
    run.add_argument("--prompt-version", type=int, default=None)
    run.add_argument("--limit", type=int, default=None, help="only the first N cases")
    run.add_argument("--tag", default=None, help="only cases carrying this tag")
    run.add_argument("--replay", action="store_true", help="use recorded answers, no model calls")
    run.add_argument("--record", action="store_true", help="save this run's answers for replay")
    run.add_argument("--force", action="store_true", help="record even a partial or failing run")
    run.add_argument("--min-pass-rate", type=float, default=None)
    run.add_argument("--json", action="store_true", help="print the report as JSON")

    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    all_cases = load_cases(args.task)
    cases = all_cases
    if args.tag:
        cases = [c for c in cases if args.tag in c.tags]
    if args.limit is not None:
        cases = cases[: args.limit]
    if not cases:
        print("no cases selected")
        return 2

    provider = None
    replay = None
    if args.replay:
        replay = load_recording(args.task)
    elif not settings.OPENAI_API_KEY:
        print("OPENAI_API_KEY is not set; use --replay to score recorded answers")
        return 2
    else:
        provider = OpenAIProvider(
            api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_BASE_URL
        )

    try:
        report, recording = await run_eval(
            args.task,
            provider=provider,
            settings=settings,
            cases=cases,
            model=args.model,
            prompt_version=args.prompt_version,
            replay=replay,
        )
    finally:
        if provider is not None:
            await provider.aclose()

    print(_as_json(report) if args.json else report.summary())

    if args.record:
        print(_record(args, report, recording, expected_cases=len(all_cases)))

    floor = args.min_pass_rate
    if floor is None:
        floor = REPLAY_MIN_PASS_RATE if args.replay else LIVE_MIN_PASS_RATE
    return 0 if not report.errors and report.pass_rate >= floor else 1


def _record(
    args: argparse.Namespace, report: EvalReport, recording: Recording, *, expected_cases: int
) -> str:
    """A recording is the baseline a replay is judged against, so a bad run must not become one."""
    problems = []
    if report.errors:
        problems.append(f"{len(report.errors)} case(s) errored")
    if report.pass_rate < 1.0:
        problems.append(f"pass rate is {report.pass_rate:.1%}")
    if len(recording.answers) != expected_cases:
        problems.append(f"only {len(recording.answers)} of {expected_cases} cases were run")
    if problems and not args.force:
        return "not recorded: " + "; ".join(problems) + " (use --force to record anyway)"

    path = save_recording(args.task, recording)
    return f"recorded {len(recording.answers)} answers to {path}"


def _as_json(report: EvalReport) -> str:
    return json.dumps(
        {
            "task": report.task,
            "model": report.model,
            "prompt_version": report.prompt_version,
            "cases": report.total,
            "passed": report.passed,
            "pass_rate": round(report.pass_rate, 4),
            "field_accuracy": round(report.field_accuracy, 4),
            "errors": len(report.errors),
            "cost_micros": report.cost_micros,
            "cost_micros_per_1k": report.cost_micros_per_1k,
        },
        indent=2,
    )


if __name__ == "__main__":
    sys.exit(main())
