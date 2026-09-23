"""The eval sets and the runner (docs/07 evals).

These never call a model: the runner replays answers recorded from a live run, which is what CI
needs — proof that the prompts, schemas, scorers and spec builder still fit together.
"""

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.ai.config import TASKS, model_for
from app.ai.evals.runner import CASE_ROOT, RECORDING_ROOT, load_cases, load_recording, run_eval
from app.ai.evals.scorers import SPEC_CHECKS, SUPERSET_FLAG, score_spec_parse
from app.ai.prompt_registry import get_prompt
from app.ai.schemas import get_schema
from tests.ai_support import settings

EVAL_TASKS = sorted(p.stem for p in CASE_ROOT.glob("*.jsonl"))

#: The floor a recorded baseline has to clear to be worth keeping; the phase's own criterion
#: (phases/phase-02-search.md) is 90%. The exact recorded score is asserted separately, so a
#: regression in the scorer or the spec builder still fails loudly.
MIN_PASS_RATE = 0.9

#: docs/07: start at 50 and grow. Fewer than this and a single odd case moves the number too far.
MIN_CASES = 50


def test_every_task_with_a_prompt_has_an_eval_set() -> None:
    assert set(EVAL_TASKS) == set(TASKS), (
        "a configured task without an eval set cannot be changed safely"
    )


@pytest.mark.parametrize("task", EVAL_TASKS)
def test_the_eval_set_is_big_enough_and_well_formed(task: str) -> None:
    cases = load_cases(task)
    assert len(cases) >= MIN_CASES
    assert len({c.id for c in cases}) == len(cases)
    for case in cases:
        assert case.input.get("query"), case.id
        assert case.expected, case.id
        assert case.tags, f"{case.id} has no tags, so it cannot be sliced"


@pytest.mark.parametrize("task", EVAL_TASKS)
def test_the_expectations_only_name_fields_the_scorer_checks(task: str) -> None:
    if task != "spec_parse":
        return
    for case in load_cases(task):
        unknown = set(case.expected) - set(SPEC_CHECKS) - {SUPERSET_FLAG}
        # A typo in an expectation key would silently never be checked.
        assert not unknown, f"{case.id} pins unknown fields: {sorted(unknown)}"


@pytest.mark.parametrize("task", EVAL_TASKS)
def test_the_eval_set_covers_more_than_one_answer(task: str) -> None:
    cases = load_cases(task)
    key = "intent"
    answers = {c.expected.get(key) for c in cases if key in c.expected}
    assert len(answers) >= 3, f"{task} only ever expects {answers}"


@pytest.mark.parametrize("task", EVAL_TASKS)
def test_every_recorded_answer_still_matches_its_schema(task: str) -> None:
    validator = Draft202012Validator(get_schema(task))
    for case_id, answer in load_recording(task).answers.items():
        validator.validate(answer)
        assert case_id.startswith(("ic-", "sp-"))


@pytest.mark.parametrize("task", EVAL_TASKS)
async def test_replaying_the_recorded_run_still_scores_full_marks(task: str) -> None:
    report, recording = await run_eval(
        task, provider=None, settings=settings(), replay=load_recording(task)
    )

    assert report.errors == [], report.summary()
    assert report.pass_rate >= MIN_PASS_RATE, report.summary()
    assert len(recording.answers) == report.total
    # A replay costs nothing; if this ever moves, the runner called a provider.
    assert report.cost_micros == 0

    # Exactly the score the baseline was taken at: the scorer and the spec builder must still
    # read these same answers the same way.
    baseline = load_recording(task).meta
    assert report.pass_rate == pytest.approx(baseline["pass_rate"])
    assert report.field_accuracy == pytest.approx(baseline["field_accuracy"])


async def test_a_case_missing_from_the_recording_is_an_error_not_a_pass() -> None:
    task = "intent_classify"
    recording = load_recording(task)
    dropped = sorted(recording.answers)[0]
    del recording.answers[dropped]

    report, _ = await run_eval(task, provider=None, settings=settings(), replay=recording)

    assert report.pass_rate < 1.0
    assert any(case_id == dropped for case_id, _ in report.errors)


async def test_a_wrong_answer_is_reported_with_its_diff() -> None:
    task = "intent_classify"
    recording = load_recording(task)
    broken = sorted(recording.answers)[0]
    recording.answers[broken] = {**recording.answers[broken], "intent": "other"}

    report, _ = await run_eval(task, provider=None, settings=settings(), replay=recording)

    failure = next(s for s in report.scores if s.case_id == broken)
    assert not failure.passed
    assert failure.diffs["intent"][1] == "other"
    assert broken in report.summary()


def test_the_recordings_carry_no_note_of_who_ran_them() -> None:
    for task in EVAL_TASKS:
        raw = (RECORDING_ROOT / f"{task}.json").read_text(encoding="utf-8")
        # Recordings are committed, so they must hold answers and nothing else.
        assert "sk-" not in raw
        for answer in json.loads(raw)["answers"].values():
            assert isinstance(answer, dict)


@pytest.mark.parametrize("task", EVAL_TASKS)
def test_a_recording_is_only_a_baseline_for_the_prompt_that_produced_it(task: str) -> None:
    recording = load_recording(task)
    config = TASKS[task]
    adopted = get_prompt(task, config.prompt_version)

    # Without this, adopting a new prompt would leave CI replaying the old one's answers and
    # reporting a green that says nothing about the prompt now in use.
    assert recording.prompt_version == adopted.ref
    assert recording.model == model_for(config.tier, settings())
    assert recording.meta.get("recorded_at")
    assert recording.meta.get("cases") == len(load_cases(task))


def test_the_scorer_judges_meaning_not_phrasing() -> None:
    def draft(**overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "intent": "prospecting",
            "industry_terms": ["coaching centre"],
            "location": {"country": "IN", "states": [], "cities": ["Kota"], "radius_km": None},
            "employee_count": {"gte": None, "lte": None},
            "has_website": None,
            "keywords": {"must": ["NEET preparation"], "should": [], "not": []},
            "fields": [],
            "depth": None,
            "max_results": None,
            "exclude_existing": False,
            "seed_company": None,
            "unsupported": [],
            "ambiguities": [],
            "confidence": 0.9,
        }
        base.update(overrides)
        return base

    # By default the words must match: adding a qualifier narrows a search exactly as much as
    # dropping one widens it, and the prompt tells the model to drop quality words.
    assert not score_spec_parse({"keywords_must": ["NEET"]}, draft()).passed

    # A case can allow extra words where more than one honest reading exists.
    assert score_spec_parse({"keywords_must": ["NEET"], SUPERSET_FLAG: True}, draft()).passed

    # Even then, dropping a word that narrows the search is not allowed.
    dropped = draft(industry_terms=["school"])
    assert not score_spec_parse({"industry": ["cbse school"], SUPERSET_FLAG: True}, dropped).passed

    # And a plain wrong value still fails.
    assert not score_spec_parse({"cities": ["Jaipur"]}, draft()).passed


def test_a_case_that_pins_nothing_is_refused_rather_than_passing() -> None:
    with pytest.raises(ValueError, match="pins nothing"):
        score_spec_parse({}, {"intent": "prospecting"})
