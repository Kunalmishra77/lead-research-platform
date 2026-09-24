"""Turning a discovered business into rows (app/db/graph.py, task 2.11).

The database enforces most of what matters here — a domain that still has "www." on it, a phone
that is not E.164, a country that is not two letters are all CHECK violations, and a violation in
the middle of a batch loses the whole search. So these tests are mostly about the normalisation
that has to happen before a value can be offered to the column at all.
"""

import pytest

from app.db.graph import normalized_name, registrable_domain, to_e164


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.example.com/contact", "example.com"),
        ("https://example.com", "example.com"),
        ("http://EXAMPLE.CO.IN/", "example.co.in"),
        ("https://shop.example.com/a/b?c=d", "shop.example.com"),
        ("example.com", "example.com"),
        ("https://example.com:8443/x", "example.com"),
        ("https://user@example.com/x", "example.com"),
    ],
)
def test_a_website_becomes_the_bare_host_the_column_accepts(url: str, expected: str) -> None:
    # companies.primary_domain CHECKs that there is no leading "www.", and it is the dedupe key:
    # storing "www.example.com" beside "example.com" would make one business into two.
    assert registrable_domain(url) == expected


@pytest.mark.parametrize(
    "url", [None, "", "not a url", "https://", "localhost", "https://exa mple.com", "//"]
)
def test_an_unusable_website_is_nothing_rather_than_a_guess(url: str | None) -> None:
    # A wrong domain here merges two unrelated businesses into one company, which no later stage
    # can untangle. None is the safe answer.
    assert registrable_domain(url) is None


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        ("+91 20 1234 5678", "+912012345678"),
        ("+1 (415) 555-0123", "+14155550123"),
        ("  +44 20 7946 0958 ", "+442079460958"),
    ],
)
def test_an_international_number_keeps_only_its_digits(phone: str, expected: str) -> None:
    # company_locations.phone_e164 CHECKs ^[+][1-9][0-9]{6,14}$ — no spaces, no punctuation.
    assert to_e164(phone) == expected


@pytest.mark.parametrize(
    "phone",
    [
        None,
        "",
        # A national number: knowing which leading digits are a trunk prefix is country-specific,
        # and inventing a country code would put a real call through to the wrong country.
        "020 1234 5678",
        "(415) 555-0123",
        "+0 20 1234",  # a country code cannot start with zero
        "+12",  # too short to be anyone
        "+1234567890123456789",  # longer than E.164 allows
    ],
)
def test_a_number_we_cannot_prove_is_e164_is_left_out(phone: str | None) -> None:
    # It is still stored as a `phone` field value with its provenance; it is simply not claimed
    # to be E.164 here. Real normalisation arrives with app/normalize/phone.py in Phase 3.
    assert to_e164(phone) is None


def test_a_name_is_normalised_for_matching_not_for_display() -> None:
    # companies.normalized_name is NOT NULL and is what later resolution blocks on; canonical_name
    # keeps what the source actually said.
    assert normalized_name("Dr. Sanap's Clinic") == "dr sanap s clinic"
    assert normalized_name("  BLUE   Tokai  ") == "blue tokai"
    assert normalized_name("Café Müller") == "café müller"
