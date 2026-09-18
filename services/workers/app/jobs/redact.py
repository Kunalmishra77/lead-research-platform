"""Scrubs error text before logs, the DLQ, progress events or Sentry see it (docs/10 hygiene)."""

import re

# SQLAlchemy appends "[SQL: ...]" and "[parameters: (...)]"; parameters are user data.
_SQL_PARAMS = re.compile(r"\[parameters: .*?\](?=\s*(?:\(|\[|$))", re.DOTALL)
# Postgres constraint details: Key (email)=(someone@example.com) already exists.
_PG_KEY = re.compile(r"(\bKey \([^)]*\)=\()[^)]*\)")
_URL_QUERY = re.compile(r"(https?://[^\s?#'\"]+)[?#][^\s'\"]*")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Credential-looking pairs; a value starting with "(" is Postgres wording, handled above.
_BEARER = re.compile(r"(?i)(bearer|token|key|secret|password)([=: ]+)(?!\()[^\s,;'\"&]+")
# OAuth codes only in query form; "status code: 503" stays readable.
_CODE = re.compile(r"(?i)(\bcode=)[^\s,;'\"&]+")
MAX_ERROR_TEXT = 500


def redact(text: str) -> str:
    text = _SQL_PARAMS.sub("[parameters: [redacted]]", text)
    text = _PG_KEY.sub(r"\1[redacted])", text)
    text = _URL_QUERY.sub(r"\1?[redacted]", text)
    text = _EMAIL.sub("[email]", text)
    text = _BEARER.sub(r"\1\2[redacted]", text)
    text = _CODE.sub(r"\1[redacted]", text)
    return text[:MAX_ERROR_TEXT]
