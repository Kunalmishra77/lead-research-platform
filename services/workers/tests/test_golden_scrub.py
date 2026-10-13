"""The corpus scrubber (task 3.14, ADR-0020).

Two properties, and the first one is why this file is longer than the module deserves.

**Nothing of the original survives.** The repository is public and 14 of 22 surveyed homepages
carried a named individual's address. A scrubber that misses one value publishes it, and the
miss is invisible -- the fixture looks fine, the tests pass, and somebody's email is on GitHub.
So the tests here are mostly adversarial: numbers at the end of sentences, inside attributes,
written with every separator, obfuscated, and in JSON.

**Shape survives.** The substitution is also the label (ADR-0020), so a fixture has to keep
every property the extractors read: a national number's leading zero, a landline's area code, a
`mailto:` being a link rather than text, an obfuscated `(at)` still obfuscated.
"""

import re

import pytest

from app.golden.scrub import scrub

PAGE = """<html><head>
<script type="application/ld+json">{"@type":"Dentist","telephone":"+91 124 456 7890",
"email":"info@drsharma.in"}</script>
</head><body>
<h1>Dr Sharma Dental</h1>
<p>We have been serving Gurugram families for over twenty years with gentle modern dentistry.</p>
<a href="mailto:priya.sharma@drsharma.in">Email Dr Priya</a>
<a href="tel:+919711174449">Call us</a>
<a href="https://wa.me/919811223344">WhatsApp</a>
<p>Reach us at reception (at) drsharma (dot) in or on 097111 74449.</p>
<p>Pin code 122002. Established 2004.</p>
<p>Site by Webwala, call 9999888877 for a website.</p>
</body></html>"""

#: Every contact value in PAGE, in each spelling it appears in.
ORIGINALS = (
    "drsharma.in",
    "priya.sharma",
    "info@drsharma",
    "reception (at) drsharma",
    "9711174449",
    "919811223344",
    "097111 74449",
    "124 456 7890",
    "1244567890",
    "9999888877",
)


def test_nothing_of_the_original_survives() -> None:
    """The property that makes the corpus publishable. Everything else here supports it."""
    fixture = scrub(PAGE, slug="dr-sharma-dental").html
    leaked = [value for value in ORIGINALS if value in fixture]
    assert leaked == [], f"the scrubber published: {leaked}"


@pytest.mark.parametrize(
    "line",
    [
        # A number at the end of a sentence. This one was being missed: the trailing guard was
        # `(?![\\w.])`, which a full stop fails, so a real phone number was left in a fixture.
        "<p>Call 097111 74449.</p>",
        "<p>Call 097111 74449</p>",
        "<p>Call 097111 74449, any time</p>",
        "<p>(097111 74449)</p>",
        "<p>Phone: +91-97111-74449.</p>",
        "<p>Ph. 0124 456 7890 / 0124 456 7891</p>",
        '<p>WhatsApp <a href="https://wa.me/919711174449">here</a>.</p>',
        '<div data-phone="9711174449">Call</div>',
    ],
)
def test_a_number_is_found_whatever_sits_next_to_it(line: str) -> None:
    """Adversarial, because a boundary that fails closed leaves real numbers in the corpus."""
    fixture = scrub(line, slug="probe").html
    assert "74449" not in fixture or "4567890" not in fixture
    assert not re.search(r"9711174449|97111 74449|0124 456 789", fixture), fixture


@pytest.mark.parametrize(
    "line",
    [
        '<a href="mailto:priya@clinic.in">mail</a>',
        "<p>priya@clinic.in</p>",
        "<p>priya (at) clinic (dot) in</p>",
        "<p>priya&#64;clinic.in</p>",
        '<p>priya&#0064;clinic.in</p>',
        '<span data-email="priya@clinic.in">x</span>',
        '<p>Email: PRIYA@CLINIC.IN</p>',
    ],
)
def test_an_address_is_found_however_it_is_written(line: str) -> None:
    fixture = scrub(line, slug="probe").html
    assert "priya" not in fixture.lower(), fixture
    assert "clinic.in" not in fixture.lower(), fixture


def test_a_national_number_keeps_its_leading_zero() -> None:
    """That zero is the trunk prefix, and removing it is what `app/normalize/phones.py` is for.

    A corpus that normalised it away would stop testing the defect that dropped 481 numbers for
    want of a `+`.
    """
    [phone] = [s for s in scrub("<p>097111 74449</p>", slug="p").substitutions]
    assert phone.replacement.startswith("0")
    assert " " in phone.replacement, "and the grouping it was written in"


def test_a_landline_stays_a_landline() -> None:
    """`phone_type` is a stored field. Replacing a Gurugram `124` with a mobile prefix would
    make every landline in the corpus a mobile and delete the only test of it."""
    [phone] = scrub('<p>+91 124 456 7890</p>', slug="p").substitutions
    assert phone.replacement.startswith("+91 124"), phone.replacement


def test_a_mobile_stays_a_mobile() -> None:
    [phone] = scrub("<p>+91 97111 74449</p>", slug="p").substitutions
    digits = re.sub(r"\D", "", phone.replacement)
    assert digits.startswith("91")
    assert digits[2] in "6789", "India's mobile ranges start 6 to 9"


def test_a_role_account_keeps_its_mailbox_name() -> None:
    """Which mailbox it is, is the fact `email_role` records. Replacing `info` with `box17`
    would delete the thing under test."""
    [email] = scrub('<a href="mailto:info@clinic.in">x</a>', slug="p").substitutions
    assert email.replacement.startswith("info@")


def test_a_person_does_not_keep_theirs() -> None:
    """The opposite case, and the reason the corpus can be published at all."""
    [email] = scrub('<a href="mailto:priya.sharma@clinic.in">x</a>', slug="p").substitutions
    assert "priya" not in email.replacement
    assert "sharma" not in email.replacement
    # The shape is kept, because `first.last@` and `info@` are different evidence.
    assert "." in email.replacement.split("@")[0]


def test_an_obfuscated_address_stays_obfuscated() -> None:
    """Decoding `(at)` is a case the extractor has to handle; normalising it here would delete
    the test rather than pass it."""
    fixture = scrub("<p>reception (at) clinic (dot) in</p>", slug="p").html
    assert "(at)" in fixture


def test_the_domain_keeps_its_top_level_domain() -> None:
    """A `.test` domain is refused by `has_real_tld`, so a corpus full of them would send every
    fixture down a code path no real page takes (ADR-0020)."""
    [email] = scrub('<a href="mailto:x@clinic.co.in">x</a>', slug="p").substitutions
    assert email.replacement.endswith(".in")


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ('<a href="mailto:x@c.in">m</a>', "mailto"),
        ('<a href="tel:+919711174449">c</a>', "tel"),
        ('<a href="https://wa.me/919711174449">w</a>', "whatsapp"),
        ('<script type="application/ld+json">{"email":"x@c.in"}</script>', "jsonld"),
        ("<p>x@c.in</p>", "text"),
    ],
)
def test_where_a_value_sat_is_recorded(markup: str, expected: str) -> None:
    """The extractors weigh a `mailto:` above text (`CONFIDENCE_LINKED` against
    `CONFIDENCE_TEXT`), so the corpus has to know which it was or the report cannot tell a weak
    find from a strong one."""
    [substitution] = scrub(markup, slug="p").substitutions
    assert substitution.context == expected


def test_a_text_address_is_not_labelled_by_a_link_further_up_the_page() -> None:
    """It was. `_context_of` read a window of a hundred characters and picked up the `wa.me` on
    the line above, labelling a plain text address as a WhatsApp link."""
    page = '<a href="https://wa.me/919811223344">WhatsApp</a>\n<p>reception@clinic.in</p>'
    emails = [s for s in scrub(page, slug="p").substitutions if s.kind == "email"]
    assert [s.context for s in emails] == ["text"]


def test_the_same_page_scrubs_the_same_way_twice() -> None:
    """Re-harvesting a page must not churn the corpus: a diff full of renumbered fixtures is a
    diff nobody reads."""
    first = scrub(PAGE, slug="dr-sharma-dental")
    second = scrub(PAGE, slug="dr-sharma-dental")
    assert first.html == second.html
    assert [s.replacement for s in first.substitutions] == [
        s.replacement for s in second.substitutions
    ]


def test_one_value_written_twice_becomes_one_value() -> None:
    """A phone number in the header and the footer is one number, and the labels have to say so
    or the report counts one find as two misses."""
    page = '<a href="tel:+919711174449">call</a><p>or +91 97111 74449</p>'
    phones = {s.replacement for s in scrub(page, slug="p").substitutions if s.kind == "phone"}
    digits = {re.sub(r"\D", "", p) for p in phones}
    assert len(digits) == 1, phones


def test_prose_is_dropped_and_markup_is_not() -> None:
    """The paragraphs are the owner's writing and no contact extractor reads them. The tags are
    what the extractors walk, so they stay."""
    fixture = scrub(PAGE, slug="dr-sharma-dental").html
    assert "serving Gurugram families" not in fixture
    assert "[prose removed for the corpus]" in fixture
    assert '<script type="application/ld+json">' in fixture
    assert "<h1>Dr Sharma Dental</h1>" in fixture, "a heading is not prose"


def test_a_line_that_holds_a_number_is_never_dropped_as_prose() -> None:
    """Dropping it would delete a label as well as the evidence, and the report would then count
    a value the corpus no longer contains as a miss."""
    page = (
        "<p>Our clinic has been open since 2004 and we are proud to serve the whole "
        "neighbourhood, do call 097111 74449 to book.</p>"
    )
    fixture = scrub(page, slug="p").html
    assert "[prose removed" not in fixture
    assert "097111 74449" not in fixture, "and it is still substituted"


def test_a_postcode_is_left_alone() -> None:
    """Six digits, not a phone number, and a public postcode rather than anybody's contact
    detail -- so the corpus can test `postal_code` extraction against a real value."""
    fixture = scrub("<p>Pin code 122002</p>", slug="p").html
    assert "122002" in fixture


def test_an_empty_page_is_not_an_error() -> None:
    result = scrub("", slug="p")
    assert result.html == ""
    assert result.substitutions == []
