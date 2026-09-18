import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from leadforge_contracts import job_envelope, progress_event, research_spec
from leadforge_contracts._wire import WireModel

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

MODELS: dict[str, type[WireModel]] = {
    "job-envelope": job_envelope.JobEnvelope,
    "progress-event": progress_event.ProgressEvent,
    "research-spec": research_spec.ResearchSpec,
}

# JSON Schema rules the Pydantic generator cannot express. The TS validator enforces them
# before publishing, and the workers envelope parser re-checks them (task 1.9).
TS_ONLY_RULES = {
    ("job-envelope", "research-job-without-org.json"),  # if/then: research_job_id requires org_id
}


def _cases(kind: str) -> list[tuple[str, str, dict[str, object]]]:
    out = []
    for contract in MODELS:
        for path in sorted((FIXTURES / contract / kind).glob("*.json")):
            out.append((contract, path.name, json.loads(path.read_text(encoding="utf-8"))))
    return out


@pytest.mark.parametrize(("contract", "name", "data"), _cases("valid"))
def test_valid_fixture_round_trips_exactly(contract: str, name: str, data: dict[str, object]) -> None:
    model = MODELS[contract].model_validate(data)
    assert model.to_wire_dict() == data
    assert json.loads(model.to_wire()) == data


@pytest.mark.parametrize(("contract", "name", "data"), _cases("invalid"))
def test_rejects_invalid(contract: str, name: str, data: dict[str, object]) -> None:
    if (contract, name) in TS_ONLY_RULES:
        pytest.skip("rule enforced by the TS validator and the workers envelope parser")
    with pytest.raises(ValidationError):
        MODELS[contract].model_validate(data)


def test_python_built_envelope_omits_unset_optionals() -> None:
    spec = research_spec.ResearchSpec(
        spec_version=1,
        entity="company",
        intent="single_company",
        filters=research_spec.Filters(),
        fields=["name"],
        depth="quick",
        limits=research_spec.Limits(max_results=1, max_credits=10),
    )
    wire = spec.to_wire_dict()
    assert wire["filters"] == {}
    assert "keywords" not in wire
