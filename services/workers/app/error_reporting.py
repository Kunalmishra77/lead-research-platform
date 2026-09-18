"""Sentry error reporting (task 1.15): off unless SENTRY_DSN_WORKERS is set.

Only errors are sent. No local variables, no default PII, no stdlib-logging capture (structlog does
not use it, but httpx/asyncio would log full URLs), no trace headers on outgoing requests (crawled
sites must not see our ids). Exception text, messages and breadcrumbs go through `redact()`.
"""

import time
from collections.abc import Callable
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.types import Breadcrumb, BreadcrumbHint, Event, Hint

from app.jobs.errors import ErrorClass
from app.jobs.redact import redact

# Expected outcomes, not defects: a compliance stop is the crawler working as designed.
_NOT_REPORTED = frozenset({ErrorClass.ACCESS_RESTRICTED, ErrorClass.BUDGET_EXHAUSTED})
# Infra errors repeat every loop iteration during an outage: report each kind once a minute.
_INFRA_REPORT_INTERVAL_S = 60.0
_last_infra_report: dict[str, float] = {}


def _strip_query(url: str) -> str:
    for sep in ("?", "#"):
        url = url.split(sep, 1)[0]
    return url


def scrub_breadcrumb(crumb: Breadcrumb, _hint: BreadcrumbHint | None = None) -> Breadcrumb | None:
    if crumb.get("category") == "console":
        return None
    message = crumb.get("message")
    if isinstance(message, str):
        crumb["message"] = redact(message)
    data = crumb.get("data")
    if isinstance(data, dict):
        data.pop("http.query", None)
        data.pop("http.fragment", None)
        for key in ("url", "from", "to"):
            if isinstance(data.get(key), str):
                data[key] = _strip_query(data[key])
    return crumb


def scrub_event(event: Event, _hint: Hint | None = None) -> Event:
    for exc in (event.get("exception") or {}).get("values") or []:
        if isinstance(exc.get("value"), str):
            exc["value"] = redact(exc["value"])
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        entry: dict[str, Any] = logentry
        for key in ("message", "formatted"):
            if isinstance(entry.get(key), str):
                entry[key] = redact(entry[key])
        entry.pop("params", None)
    if isinstance(event.get("message"), str):
        event["message"] = redact(event["message"])
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        values = [scrub_breadcrumb(b) for b in breadcrumbs.get("values") or []]
        breadcrumbs["values"] = [b for b in values if b is not None]
    for key in ("user", "request", "extra"):
        event.pop(key, None)  # type: ignore[misc]
    return event


def configure_error_reporting(
    dsn: str | None, environment: str, traces_sample_rate: float = 0.0
) -> bool:
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        send_default_pii=False,
        include_local_variables=False,
        traces_sample_rate=traces_sample_rate if traces_sample_rate > 0 else None,
        trace_propagation_targets=[],
        integrations=[LoggingIntegration(level=None, event_level=None)],
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
    )
    return True


def report_job_failure(
    exc: BaseException,
    *,
    error_class: ErrorClass,
    job_type: str,
    job_id: str,
    org_id: str | None,
    trace_id: str | None,
) -> None:
    """Reports a job that failed for good; a no-op when Sentry is not configured."""
    if error_class in _NOT_REPORTED:
        return
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("error_class", error_class.value)
        scope.set_tag("job_type", job_type)
        scope.set_tag("job_id", job_id)
        if org_id:
            scope.set_tag("org_id", org_id)
        if trace_id:
            scope.set_tag("trace_id", trace_id)
        sentry_sdk.capture_exception(exc)


def report_exception(
    exc: BaseException, *, now: Callable[[], float] = time.monotonic, **tags: str
) -> bool:
    """Reports an infrastructure error (consumer loop, retry promotion), at most once a minute per
    exception type and tag set. Returns whether it was sent to the SDK."""
    key = f"{type(exc).__name__}|{sorted(tags.items())}"
    current = now()
    last = _last_infra_report.get(key)
    if last is not None and current - last < _INFRA_REPORT_INTERVAL_S:
        return False
    _last_infra_report[key] = current
    with sentry_sdk.new_scope() as scope:
        for name, value in tags.items():
            scope.set_tag(name, value)
        scope.fingerprint = ["infra", type(exc).__name__, *sorted(tags.values())]
        sentry_sdk.capture_exception(exc)
    return True
