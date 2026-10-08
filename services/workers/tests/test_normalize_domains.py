"""`site_domain` (added for task 3.7) next to the two functions it is easily confused with.

The three answer different questions and the crawler picks the wrong one if the difference is not
written down: `registrable_domain` keeps the whole host because `companies.primary_domain` stores
it, `registrable_stem` is the registered name alone, and `site_domain` is the name plus its suffix.
"""

import pytest

from app.normalize.domains import registrable_domain, registrable_stem, site_domain


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://clinic.example/a", "clinic.example"),
        ("https://www.clinic.example/a", "clinic.example"),
        ("https://blog.clinic.example/a", "clinic.example"),
        ("https://a.b.c.clinic.example/a", "clinic.example"),
        # A two-label public suffix keeps three labels, or every .co.in site would read as "co.in"
        # and two unrelated clinics would look like one site.
        ("https://clinic.co.in/a", "clinic.co.in"),
        ("https://www.clinic.co.in/a", "clinic.co.in"),
        ("https://blog.clinic.co.in/a", "clinic.co.in"),
        ("https://clinic.example:8443/a", "clinic.example"),
        ("clinic.example", "clinic.example"),
    ],
)
def test_site_domain_collapses_subdomains(url: str, expected: str) -> None:
    assert site_domain(url) == expected


@pytest.mark.parametrize("url", [None, "", "localhost", "https://localhost/a", "co.in"])
def test_site_domain_is_none_when_there_is_no_site(url: str | None) -> None:
    # None rather than a guess: it decides what gets crawled, and a wrong answer there means
    # fetching someone else's pages under this lead's name.
    assert site_domain(url) is None


def test_a_lookalike_domain_is_not_the_same_site() -> None:
    assert site_domain("https://clinic.example.evil.test/a") == "evil.test"


def test_the_three_functions_answer_three_different_questions() -> None:
    url = "https://blog.clinic.co.in/about"
    assert registrable_domain(url) == "blog.clinic.co.in"  # the host, for primary_domain
    assert site_domain(url) == "clinic.co.in"  # the site, for deciding what to crawl
    assert registrable_stem(url) == "clinic"  # the registered name, for matching a business name
