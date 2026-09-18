"""Scrubs error text before logs, the DLQ or progress events see it (docs/10 logging hygiene)."""

import re

_URL_QUERY = re.compile(r"(https?://[^\s?#'\"]+)\?[^\s'\"]*")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BEARER = re.compile(r"(?i)(bearer|token|key|secret|password)([=: ]+)[^\s,;'\"]+")
MAX_ERROR_TEXT = 500


def redact(text: str) -> str:
    text = _URL_QUERY.sub(r"\1?[redacted]", text)
    text = _EMAIL.sub("[email]", text)
    text = _BEARER.sub(r"\1\2[redacted]", text)
    return text[:MAX_ERROR_TEXT]
