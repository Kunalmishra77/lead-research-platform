import pytest

from app.devdns import install_dev_dns
from app.jobs.envelope import dlq_for, parse_envelope, stream_for
from app.jobs.errors import ErrorClass, InvalidInputError, ParseFailedError, classify
from app.jobs.redact import redact
from tests.conftest import EnvelopeFactory


def test_error_classification() -> None:
    assert classify(ParseFailedError("x")) is ErrorClass.PARSE_FAILED
    assert classify(ValueError("unexpected")) is ErrorClass.TRANSIENT


def test_stream_naming() -> None:
    assert stream_for("crawl_http") == "jobs:crawl_http"
    assert dlq_for("jobs:crawl_http") == "dlq:jobs:crawl_http"


def test_parse_envelope_round_trip(make_envelope: EnvelopeFactory) -> None:
    envelope = make_envelope()
    assert parse_envelope(envelope.to_wire()) == envelope


def test_parse_envelope_rejects_strict_type_violations(make_envelope: EnvelopeFactory) -> None:
    wire = make_envelope().to_wire().replace('"attempt":1', '"attempt":"1"')
    with pytest.raises(InvalidInputError):
        parse_envelope(wire)


def test_dev_dns_never_installs_in_production() -> None:
    assert install_dev_dns(enabled=True, node_env="production") is False
    assert install_dev_dns(enabled=False, node_env="development") is False


def test_error_text_is_redacted() -> None:
    text = redact(
        "GET https://api.example.com/v1/x?key=SECRET123&q=a failed for ann@example.com token=abc"
    )
    assert "SECRET123" not in text
    assert "ann@example.com" not in text
    assert "abc" not in text
    assert "https://api.example.com/v1/x?[redacted]" in text
