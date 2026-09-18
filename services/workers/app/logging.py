"""Structured JSON logs carrying trace_id / org_id / job_id (CLAUDE.md "No silent failures")."""

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

_SECRET_KEYS = ("password", "secret", "token", "authorization", "api_key", "access_key")


def _mask_secrets(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(event):
        if any(marker in key.lower() for marker in _SECRET_KEYS):
            event[key] = "[redacted]"
    return event


def configure_logging(level: str = "info") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _mask_secrets,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
