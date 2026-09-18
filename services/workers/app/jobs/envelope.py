"""Envelope parsing on top of the generated contract (packages/contracts)."""

from leadforge_contracts.job_envelope import JobEnvelope
from pydantic import ValidationError

from app.jobs.errors import InvalidInputError

STREAM_PREFIX = "jobs:"
DLQ_PREFIX = "dlq:"
ENVELOPE_FIELD = "envelope"


def stream_for(pool: str) -> str:
    return f"{STREAM_PREFIX}{pool}"


def dlq_for(stream: str) -> str:
    return f"{DLQ_PREFIX}{stream}"


def parse_envelope(raw: str | bytes) -> JobEnvelope:
    """Validates the wire JSON. Rules the generator cannot express are re-checked here."""
    try:
        envelope = JobEnvelope.model_validate_json(raw)
    except ValidationError as exc:
        raise InvalidInputError(f"invalid job envelope: {exc.error_count()} error(s)") from exc
    # Tenant rule (job-envelope.schema.json if/then): research work must carry its org.
    if envelope.research_job_id is not None and envelope.org_id is None:
        raise InvalidInputError("envelope with research_job_id must have org_id")
    return envelope
