"""ADR-0013's boundary, asserted rather than trusted.

Scrapling is here for its parser. Its `fetchers` extra pulls `curl_cffi` (TLS fingerprint
impersonation) and `patchright` (a Playwright patched to be undetected), and its `StealthyFetcher`
advertises bypassing Cloudflare challenges -- which CLAUDE.md forbids in the same sentence as
CAPTCHAs and logins.

The ADR chose to leave those packages out of the environment entirely rather than install them and
promise not to call them. This test is what makes that a fact instead of an intention: adding
`scrapling[fetchers]`, or a transitive dependency that drags them in, fails here with a sentence
explaining why, instead of quietly handing the next person a `StealthyFetcher` one import away.
"""

import importlib.util

import pytest

#: Installed only to evade bot detection. Nothing else in the stack has a use for them.
FORBIDDEN_MODULES = ("curl_cffi", "patchright", "browserforge")


@pytest.mark.parametrize("module", FORBIDDEN_MODULES)
def test_evasion_libraries_are_absent_from_the_environment(module: str) -> None:
    found = importlib.util.find_spec(module)
    assert found is None, (
        f"{module} is installed. It exists to defeat bot detection, which ADR-0013 and CLAUDE.md "
        f"rule out. If this arrived with scrapling[fetchers], the extra is the thing to remove; if "
        f"a new dependency pulled it in, that dependency needs an ADR of its own."
    )


def test_the_parser_scrapling_is_here_for_is_importable() -> None:
    from scrapling import Selector  # noqa: PLC0415 - imported here to prove it resolves

    page = Selector(
        '<html><body><a class="mail" href="mailto:hi@example.com">Email us</a></body></html>'
    )
    # `::attr()` and `::text` are the pseudo-elements this version supports; `.get()` takes the
    # first match. Checked against 0.4.15 rather than remembered -- there is no `css_first`.
    assert page.css("a.mail::attr(href)").get() == "mailto:hi@example.com"
    assert page.css("a.mail::text").get() == "Email us"


def test_importing_a_stealth_fetcher_fails_here_rather_than_in_production() -> None:
    # Not a style check: this import is the one obvious next step for anyone reading Scrapling's
    # own documentation, and it would be a compliance breach rather than a design disagreement.
    with pytest.raises(Exception):  # noqa: B017 - the type depends on the missing package
        from scrapling.fetchers import StealthyFetcher  # noqa: F401,PLC0415
