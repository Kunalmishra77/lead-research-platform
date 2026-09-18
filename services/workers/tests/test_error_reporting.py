"""Sentry wiring: off without a DSN, scrubbed events, expected outcomes and floods not reported."""

from typing import Any

import pytest
import sentry_sdk
from redis.asyncio import Redis

from app import error_reporting
from app.error_reporting import (
    configure_error_reporting,
    report_exception,
    report_job_failure,
    scrub_breadcrumb,
    scrub_event,
)
from app.jobs import consumer as consumer_module
from app.jobs.context import JobContext
from app.jobs.errors import ErrorClass, TransientError
from app.jobs.publisher import publish
from app.jobs.redact import redact
from app.jobs.registry import HandlerRegistry
from app.jobs.retry import delayed_key, promote_due
from tests.conftest import STREAM, ConsumerFactory, EnvelopeFactory


def test_disabled_without_dsn() -> None:
    assert configure_error_reporting(None, "test") is False
    assert configure_error_reporting("", "test") is False
    assert sentry_sdk.get_client().is_active() is False


def test_redact_removes_sql_parameters_and_constraint_values() -> None:
    text = redact(
        "(asyncpg.exceptions.UniqueViolationError) duplicate key\n"
        "DETAIL:  Key (email)=(a@b.co) already exists.\n"
        "[SQL: INSERT INTO app.contacts (email) VALUES ($1)]\n"
        "[parameters: ('a@b.co', 'Jane Doe')]\n"
        "(Background on this error at: https://sqlalche.me/e/20/gkpj)"
    )
    assert "a@b.co" not in text
    assert "Jane Doe" not in text
    assert "Key (email)=([redacted])" in text
    assert "[parameters: [redacted]]" in text
    assert "[SQL: INSERT INTO app.contacts" in text


def test_scrub_event_redacts_text_and_drops_user_request_extra() -> None:
    event: Any = {
        "exception": {"values": [{"type": "X", "value": "failed for a@b.co token=abc123"}]},
        "logentry": {
            "message": "fetch %s",
            "formatted": "fetch https://x.test/p?key=SECRET",
            "params": ["https://x.test/p?key=SECRET"],
        },
        "breadcrumbs": {
            "values": [
                {"category": "console", "message": "a@b.co"},
                {
                    "category": "httplib",
                    "data": {"url": "https://maps.test/api?key=K", "http.query": "key=K"},
                },
            ]
        },
        "user": {"id": "u", "email": "a@b.co"},
        "request": {"headers": {"Authorization": "Bearer t"}},
        "extra": {"payload": {"email": "a@b.co"}},
    }
    out: Any = scrub_event(event)
    serialized = repr(out)
    assert "a@b.co" not in serialized
    assert "abc123" not in serialized
    assert "SECRET" not in serialized
    assert "key=K" not in serialized
    assert "params" not in out["logentry"]
    assert out["breadcrumbs"]["values"] == [
        {"category": "httplib", "data": {"url": "https://maps.test/api"}}
    ]
    for key in ("user", "request", "extra"):
        assert key not in out


def test_scrub_breadcrumb_drops_console_and_strips_navigation() -> None:
    assert scrub_breadcrumb({"category": "console", "message": "x"}) is None
    crumb: Any = scrub_breadcrumb({"category": "nav", "data": {"from": "/a?x=1", "to": "/b#t"}})
    assert crumb["data"] == {"from": "/a", "to": "/b"}


@pytest.mark.parametrize(
    ("error_class", "reported"),
    [
        (ErrorClass.PARSE_FAILED, True),
        (ErrorClass.TRANSIENT, True),
        (ErrorClass.ACCESS_RESTRICTED, False),
        (ErrorClass.BUDGET_EXHAUSTED, False),
    ],
)
def test_report_job_failure_skips_expected_outcomes(
    monkeypatch: pytest.MonkeyPatch, error_class: ErrorClass, reported: bool
) -> None:
    captured: list[BaseException] = []
    monkeypatch.setattr(error_reporting.sentry_sdk, "capture_exception", captured.append)
    report_job_failure(
        ValueError("boom"),
        error_class=error_class,
        job_type="system.ping",
        job_id="j1",
        org_id=None,
        trace_id="t1",
    )
    assert bool(captured) is reported


def test_infra_errors_are_reported_at_most_once_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[BaseException] = []
    monkeypatch.setattr(error_reporting.sentry_sdk, "capture_exception", captured.append)
    monkeypatch.setattr(error_reporting, "_last_infra_report", {})
    clock = iter([100.0, 110.0, 161.0])
    tick = lambda: next(clock)  # noqa: E731
    assert report_exception(ConnectionError("down"), now=tick, stream="s") is True
    assert report_exception(ConnectionError("down"), now=tick, stream="s") is False
    assert report_exception(ConnectionError("down"), now=tick, stream="s") is True
    assert len(captured) == 2


async def test_consumer_reports_only_the_final_failure(
    monkeypatch: pytest.MonkeyPatch,
    redis: Redis,
    make_consumer: ConsumerFactory,
    make_envelope: EnvelopeFactory,
) -> None:
    reports: list[dict[str, Any]] = []
    monkeypatch.setattr(consumer_module, "report_job_failure", lambda exc, **kw: reports.append(kw))
    registry = HandlerRegistry()
    errors = [TransientError(f"boom {i}") for i in range(5)]

    @registry.register("test.echo", stage="ping")
    async def echo(_ctx: JobContext) -> None:
        raise errors.pop(0)

    consumer = make_consumer(registry)
    await consumer.ensure_group()
    await publish(redis, STREAM, make_envelope())
    for attempt in range(1, 6):
        await consumer.run_once()
        # Retries (attempts 1-4) are not reported; only the dead-lettered 5th attempt is.
        assert len(reports) == (1 if attempt == 5 else 0)
        for member in await redis.zrange(delayed_key(STREAM), 0, -1):
            await redis.zadd(delayed_key(STREAM), {member: 0})
        await promote_due(redis, STREAM)
    assert reports[0]["error_class"] is ErrorClass.TRANSIENT
    assert reports[0]["job_type"] == "test.echo"


def test_redact_keeps_status_codes_but_hides_oauth_codes() -> None:
    assert redact("upstream status code: 503") == "upstream status code: 503"
    assert "abc123" not in redact("callback code=abc123&state=x")
