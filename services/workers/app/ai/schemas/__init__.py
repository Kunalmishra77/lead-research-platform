"""Output schemas per task (docs/07): `app/ai/schemas/<task>.json`, JSON Schema 2020-12.

The schema is the contract with the model (sent as the provider's structured-output format) and
the gate on the way back, so a task cannot quietly start returning a different shape.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_ROOT = Path(__file__).resolve().parent


class SchemaNotFoundError(LookupError):
    pass


def load_schema(task: str, root: Path | None = None) -> dict[str, Any]:
    path = (root or SCHEMA_ROOT) / f"{task}.json"
    if not path.is_file():
        raise SchemaNotFoundError(f"no output schema for ai task {task}")
    schema: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return schema


@lru_cache(maxsize=32)
def get_schema(task: str) -> dict[str, Any]:
    return load_schema(task)
