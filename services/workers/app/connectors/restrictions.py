"""Detecting "you are being blocked" (docs/08 source policy, docs/06 section 4.7).

We never evade a block: the crawler stops and the target is marked `access_restricted`. This module
only recognises the situation; reacting to it is the caller's job.

Two kinds of marker, because a false positive is expensive too — a company contact page that
happens to embed a reCAPTCHA widget or a login form is exactly what Phase 3 needs to read:

* strong markers are challenge interstitials, which only ever appear when we are being stopped;
* weak markers (a captcha widget, a password field, a paywall notice) also occur on ordinary
  pages, so they only count when the response corroborates them — a blocking status code, a
  challenge header, or a body too small to be the page we asked for.
"""

import re
from collections.abc import Mapping

#: Interstitials: their presence alone means a challenge stands in the way.
_STRONG_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "bot_challenge",
        re.compile(
            r"(?i)(cf-browser-verification|__cf_chl|challenge-platform"
            r"|checking your browser before accessing"
            r"|attention required!\s*\|\s*cloudflare"
            r"|verify (you are|yourself as) (a )?human"
            r"|enable javascript and cookies to continue"
            r"|(unusual|suspicious) traffic from your (computer|network)"
            r"|automated queries"
            r"|access to this page has been denied"
            r"|(your )?request (was |has been )?blocked)"
        ),
    ),
)

#: Also seen on perfectly readable pages; need corroboration (see `_is_suspicious`).
_WEAK_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("captcha", re.compile(r"(?i)\b(captcha|recaptcha|hcaptcha|turnstile)\b")),
    (
        "login_required",
        re.compile(
            r"(?i)(<input[^>]+type=[\"']password[\"']"
            r"|please (log|sign) ?in to continue|login required)"
        ),
    ),
    (
        "paywall",
        re.compile(
            r"(?i)(subscribe to (continue|read)"
            r"|this (article|content) is for subscribers|paywall)"
        ),
    ),
)

#: Header values that identify a challenge rather than a normal response.
_CHALLENGE_HEADERS = ("cf-mitigated", "x-datadome", "x-akamai-bot")

#: Statuses that, on their own, mean we are being kept out.
_BLOCKING_STATUS = {401: "http_401", 403: "http_403", 407: "http_407", 451: "legal_block"}

#: Statuses that make a weak marker believable.
_SUSPICIOUS_STATUS = frozenset({401, 403, 407, 429, 451, 503})

#: Markers sit near the top of a page or in a trailing script, so both ends are scanned.
HEAD_SCAN_BYTES = 32 * 1024
TAIL_SCAN_BYTES = 16 * 1024

#: A response this small cannot be the page we asked for: an interstitial, not content.
SMALL_BODY_BYTES = 8 * 1024


def restriction_reason(
    status: int, headers: Mapping[str, str], body: bytes | None = None
) -> str | None:
    """Returns why the response counts as restricted, or None when it is a normal response.

    Restricted means: stop and report, never retry and never work around it.
    """
    lower = {k.lower(): v for k, v in headers.items()}
    challenged = next((h for h in _CHALLENGE_HEADERS if h in lower), None)
    if challenged:
        # A challenged 429 is still a block, but naming it keeps the rate-limit case readable.
        return "rate_limit_challenge" if status == 429 else f"bot_challenge:{challenged}"
    if status in _BLOCKING_STATUS:
        return _BLOCKING_STATUS[status]
    if not body:
        return None

    text = _scan_window(body)
    for name, pattern in _STRONG_MARKERS:
        if pattern.search(text):
            return name
    if _is_suspicious(status, len(body)):
        for name, pattern in _WEAK_MARKERS:
            if pattern.search(text):
                return name
    return None


def _is_suspicious(status: int, size: int) -> bool:
    return status in _SUSPICIOUS_STATUS or size <= SMALL_BODY_BYTES


def _scan_window(body: bytes) -> str:
    """Head + tail of the body, decoded leniently; keeps the check cheap on large pages."""
    if len(body) <= HEAD_SCAN_BYTES + TAIL_SCAN_BYTES:
        window = body
    else:
        window = body[:HEAD_SCAN_BYTES] + b"\n" + body[-TAIL_SCAN_BYTES:]
    return window.decode("utf-8", errors="ignore")
