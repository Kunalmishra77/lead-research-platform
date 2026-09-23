"""Eval sets and the runner that scores them (docs/07 evals).

`cases/<task>.jsonl` holds the set, `scorers.py` says what counts as right, and `runner.py` runs
it either live or from recorded answers. Results go in `RESULTS.md`, one row per run, so a prompt
or model change is always visible as a number that moved.
"""

from app.ai.evals.runner import EvalCase, EvalReport, load_cases, run_eval
from app.ai.evals.scorers import SCORERS, CaseScore

__all__ = [
    "SCORERS",
    "CaseScore",
    "EvalCase",
    "EvalReport",
    "load_cases",
    "run_eval",
]
