"""Recorded source responses (docs/08): tests never hit a live third-party API.

Layout: `tests/fixtures/<connector key>/<case>.json|html`, with a case for success, empty,
rate-limited and restricted/error. Record them once from a real call, then strip anything
identifying (keys, tokens, personal data) before committing.
"""

import json
from pathlib import Path
from typing import Any

FIXTURE_ROOT = Path(__file__).resolve().parent


def fixture_path(connector_key: str, case: str) -> Path:
    path = FIXTURE_ROOT / connector_key / case
    if not path.exists():
        raise FileNotFoundError(f"missing fixture {connector_key}/{case}")
    return path


def load_json(connector_key: str, case: str) -> Any:
    return json.loads(fixture_path(connector_key, f"{case}.json").read_text(encoding="utf-8"))


def load_bytes(connector_key: str, case: str) -> bytes:
    return fixture_path(connector_key, case).read_bytes()
