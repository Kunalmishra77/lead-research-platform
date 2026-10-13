"""Email normalisation (docs/06 section 6, task 3.12).

The rule lived inside the web crawler, so it applied to addresses read off a page and to nothing
else: the place index's mapping stored `place.email` as it came. What is pinned here is the rule
itself, and the property that made it worth moving -- two sources reporting the same mailbox must
produce the same string, because `field_values` keeps one current row per (entity, field) and two
spellings are two values for one address.
"""

import pytest

from app.normalize.emails import ROLE_LOCAL_PARTS, is_role, normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("info@clinic.in", "info@clinic.in"),
        ("Info@Clinic.IN", "info@clinic.in"),
        ("  priya@clinic.in  ", "priya@clinic.in"),
        ("<priya@clinic.in>", "priya@clinic.in"),
        ("priya@clinic.in.", "priya@clinic.in"),
        ("priya.sharma+leads@clinic.co.in", "priya.sharma+leads@clinic.co.in"),
    ],
)
def test_one_mailbox_has_one_spelling(raw: str, expected: str) -> None:
    """The reason this module exists.

    `field_values` holds one current row per (entity, field). `Info@Clinic.IN` from a bulk
    release and `info@clinic.in` from the clinic's own site are two values for one mailbox: the
    second retires the first, and the first's provenance goes with it.
    """
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "not an email",
        "@clinic.in",
        "priya@",
        "priya@clinic",  # no top-level domain at all
        "done@dr.bhatia",  # the real one: "treatment done@Dr.Bhatia's clinic"
        "you@example.com",
        "your@yourdomain.com",
        "name@company.com",
        "noreply@sentry.io",
        "a@wixpress.com",
    ],
)
def test_what_is_not_a_business_address_is_refused(raw: str | None) -> None:
    """`done@dr.bhatia` is not hypothetical: it went out in a customer's results, read from a
    page saying "treatment done@Dr.Bhatia's clinic". A real top-level domain is what stops it.

    The placeholder hosts are refused for every source, not only for pages. A bulk release
    carries templates nobody filled in too.
    """
    assert normalize(raw) is None


def test_an_address_longer_than_a_path_can_be_is_refused() -> None:
    """254 is what RFC 5321 allows. Something longer that contains an `@` is something else."""
    assert normalize("a" * 250 + "@clinic.in") is None
    assert normalize("a" * 40 + "@clinic.in") is not None


def test_a_quoted_local_part_is_refused_rather_than_mangled() -> None:
    """RFC 5321 allows `"odd@name"@clinic.in`; `email_validator` refuses it by default and that
    is kept. In practice a quoted local part on a business page is a parsing accident, and
    storing one would put a string no mail client will accept in front of a customer.

    Worth a test rather than a comment, because the refusal comes from a library default: if a
    future version of `email_validator` starts allowing them, this says so.
    """
    assert normalize('"odd@name"@clinic.in') is None


def test_two_addresses_run_together_yield_neither() -> None:
    """What a page with no separator between two `mailto:` texts produces.

    Refusing is the only safe answer: picking one half means guessing which, and `a@x.in` from
    `a@x.in@clinic.in` would be stored with the provenance of a page that never said it.
    """
    assert normalize("a@x.in@clinic.in") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("info@clinic.in", True),
        ("Info@Clinic.IN", True),
        (" sales@clinic.in ", True),
        ("no-reply@clinic.in", True),
        ("priya@clinic.in", False),
        ("priya.sharma@clinic.in", False),
        ("dr.bhatia@clinic.in", False),
    ],
)
def test_a_shared_mailbox_is_told_apart_from_a_person(raw: str, expected: bool) -> None:
    """docs/10 handles the two differently: `info@` is a lead, `priya@` is personal data.

    Read off the address as given, including a case or whitespace a caller has not cleaned, so
    the answer cannot depend on whether `normalize` ran first.
    """
    assert is_role(raw) is expected


def test_every_role_part_is_lowercase_and_has_no_at_sign() -> None:
    """The list is compared against a lowercased local part, so an entry with an upper-case
    letter or an `@` in it would silently never match."""
    for part in ROLE_LOCAL_PARTS:
        assert part == part.lower(), part
        assert "@" not in part, part
        assert part.strip() == part, part
