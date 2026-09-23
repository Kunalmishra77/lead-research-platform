"""Blocked-access detection: we recognise a block, we never work around it (docs/08).

Both directions matter. Missing a block would mean hammering a source that told us to stop;
crying block on an ordinary page would throw away a company page we are allowed to read.
"""

import pytest

from app.connectors.restrictions import SMALL_BODY_BYTES, restriction_reason
from app.connectors.usage import call_unit_key
from tests.fixtures import load_bytes

#: Stands in for a real company page: too big to be an interstitial.
PAGE = b"<html><body>" + b"<p>About our bakery in Pune.</p>" * 600


def test_a_normal_response_is_not_restricted() -> None:
    assert restriction_reason(200, {"content-type": "application/json"}, b'{"results": []}') is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "http_401"), (403, "http_403"), (407, "http_407"), (451, "legal_block")],
)
def test_auth_and_legal_statuses_are_restricted(status: int, expected: str) -> None:
    assert restriction_reason(status, {}) == expected


def test_a_challenge_header_wins_over_the_status_code() -> None:
    assert restriction_reason(503, {"CF-Mitigated": "challenge"}) == "bot_challenge:cf-mitigated"
    assert restriction_reason(200, {"x-akamai-bot": "1"}) == "bot_challenge:x-akamai-bot"


def test_a_plain_429_stays_a_rate_limit_but_a_challenged_one_does_not() -> None:
    assert restriction_reason(429, {"retry-after": "30"}) is None
    assert restriction_reason(429, {"x-datadome": "protected"}) == "rate_limit_challenge"


@pytest.mark.parametrize(
    "body",
    [
        b"<html><head><title>Just a moment...</title></head><body>"
        b"Checking your browser before accessing the site.</body></html>",
        b'<div id="cf-browser-verification"></div>',
        b'<script src="/cdn-cgi/challenge-platform/h/b/orchestrate"></script>',
        b"<p>Please enable JavaScript and cookies to continue</p>",
    ],
)
def test_a_challenge_interstitial_is_always_restricted(body: bytes) -> None:
    # Strong markers never appear on a page we are allowed to read, so size does not matter.
    assert restriction_reason(200, {}, body + PAGE) == "bot_challenge"


def test_a_recorded_bot_challenge_page_is_restricted() -> None:
    body = load_bytes("example_source", "restricted.html")
    assert restriction_reason(200, {"content-type": "text/html"}, body) == "bot_challenge"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"<div>Please complete the reCAPTCHA to continue</div>", "captcha"),
        (b'<form><input name="p" type="password"></form>', "login_required"),
        (b"<p>Subscribe to continue reading this story</p>", "paywall"),
    ],
)
def test_weak_markers_count_on_an_interstitial(body: bytes, expected: str) -> None:
    assert len(body) <= SMALL_BODY_BYTES
    assert restriction_reason(200, {}, body) == expected
    assert restriction_reason(403, {}, body + PAGE) == "http_403"


@pytest.mark.parametrize(
    "widget",
    [
        b'<script src="https://www.google.com/recaptcha/api.js"></script>',
        b'<form action="/login"><input type="password" name="pw"></form>',
    ],
)
def test_an_ordinary_page_carrying_a_widget_is_still_readable(widget: bytes) -> None:
    # A contact page with a captcha widget or a customer-login form is exactly what Phase 3
    # crawls for emails and phones; treating it as a block would lose the lead.
    assert restriction_reason(200, {}, PAGE + widget) is None


def test_a_weak_marker_on_a_blocking_status_is_believed() -> None:
    assert restriction_reason(429, {}, PAGE + b"complete the captcha to continue") == "captcha"


def test_markers_hidden_in_a_trailing_script_are_still_found() -> None:
    body = b"<p>filler</p>" * 40_000 + b"<script>window.__cf_chl_opt={};</script>"
    assert restriction_reason(200, {}, body) == "bot_challenge"


def test_undecodable_bytes_do_not_raise() -> None:
    assert restriction_reason(200, {}, b"\xff\xfe\x00complete the captcha") == "captcha"


def test_the_unit_key_is_stable_per_call_and_differs_per_body() -> None:
    url = "https://api.example.test/search?q=cafe"
    assert call_unit_key("example_source", url) == call_unit_key("example_source", url)
    assert call_unit_key("example_source", url) != call_unit_key("other_source", url)
    assert call_unit_key("example_source", url, b"a") != call_unit_key("example_source", url, b"b")
