"""Prints every valid fixture after a Pydantic parse + to_wire() as JSON lines (used by the TS test)."""

import json
from pathlib import Path

from leadforge_contracts import job_envelope, progress_event, research_spec

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
MODELS = {
    "job-envelope": job_envelope.JobEnvelope,
    "progress-event": progress_event.ProgressEvent,
    "research-spec": research_spec.ResearchSpec,
}

for contract, model in MODELS.items():
    for path in sorted((FIXTURES / contract / "valid").glob("*.json")):
        parsed = model.model_validate_json(path.read_text(encoding="utf-8"))
        wire = json.loads(parsed.to_wire())
        print(json.dumps({"contract": contract, "name": path.name, "wire": wire}))
