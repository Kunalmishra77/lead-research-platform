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

import json
import re

import pytest

from app.golden.scrub import _MOBILE_KINDS, is_structural, scrub
from app.normalize.phones import line_type, parse

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


@pytest.mark.parametrize(
    ("printed", "mobile"),
    [
        ("+91 97111 74449", True),
        ("097111 74449", True),
        ("9811223344", True),
        ("+91 124 456 7890", False),
        ("011 4567 8900", False),
        ("022 2654 3210", False),
        ("1800 123 4567", False),
        ("6123456789", False),
    ],
)
def test_the_replacement_is_the_same_kind_of_number(printed: str, mobile: bool) -> None:
    """The property the two tests above only approximate, asserted on the result.

    Keeping a prefix is a means, not the end. `1800 123 4567` keeps three digits and lands in
    no toll-free range; `6123456789` is a landline whose first digit is in the mobile range, so
    keeping that digit makes it a mobile. Both passed a prefix check and were wrong.
    """
    [phone] = scrub(f"<p>{printed}</p>", slug="p").substitutions
    parsed = parse(phone.replacement, "IN")
    assert parsed is not None, f"{phone.replacement} is not dialable at all"
    assert (line_type(parsed) in _MOBILE_KINDS) is mobile, line_type(parsed)


def test_an_unreadable_number_stays_unreadable() -> None:
    """Faithfulness, not failure. `+91 124 9684614` is printed on a real page and
    `phonenumbers` refuses it; a fixture that quietly repaired it would test a page nobody has,
    and the pipeline would look better on the corpus than it is in the world."""
    [phone] = scrub("<p>+91 124 9684614</p>", slug="p").substitutions
    assert parse(phone.replacement, "IN") is None, phone.replacement
    assert phone.replacement.startswith("+91 124"), "and it still looks like what it was"


def test_a_library_version_is_not_an_email() -> None:
    """`cdn.jsdelivr.net/npm/bootstrap@5.3.8` matched the email pattern, and the fixture came
    out with a mailbox where the page had a library. A TLD is never all digits, so skipping
    these cannot drop a real address."""
    html = '<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/b.css" rel="s">'
    result = scrub(html, slug="p")
    assert result.substitutions == []
    assert "bootstrap@5.3.8" in result.html


def test_a_real_address_with_a_numeric_label_is_still_substituted() -> None:
    """The guard is on the TLD, not on digits anywhere: `info@clinic24.in` is an address."""
    [sub] = scrub('<a href="mailto:info@clinic24.in">x</a>', slug="p").substitutions
    assert sub.kind == "email"


def test_inline_css_is_not_a_phone_number() -> None:
    """`<style>` elements were stripped and `style="..."` attributes were not, so a shadow
    colour became a contact value a reviewer had to read and reject by hand."""
    html = '<div style="box-shadow: 0px 0px 7px 0px #00000029">Clinic</div>'
    result = scrub(html, slug="p")
    assert result.substitutions == []
    assert 'style=""' in result.html, "the attribute stays, so the markup keeps its shape"


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


def test_a_coordinate_is_left_alone_so_the_json_stays_valid() -> None:
    """The worst of the three, because it failed silently.

    `"longitude":76.911353` was substituted to `"longitude":02.911353`, and a leading zero is
    invalid JSON. All three of that site's pages then had JSON-LD that `extruct` refused, so the
    structured extractor saw nothing and the report blamed the pipeline for a corpus artefact.
    """
    html = '<script type="application/ld+json">{"geo":{"longitude":76.911353}}</script>'
    result = scrub(html, slug="p")
    assert result.substitutions == []
    assert json.loads(result.html.split(">", 1)[1].rsplit("<", 1)[0])["geo"]["longitude"]


def test_a_date_and_an_ip_are_left_alone_too() -> None:
    """Same class: structure rather than anybody's contact details."""
    for value in ("2024-03-12", "103.197.101.15"):
        assert scrub(f"<p>{value}</p>", slug="p").substitutions == [], value


def test_a_structural_shape_is_not_a_way_to_hide_a_number() -> None:
    """The skip is narrow on purpose, and the gate asks `phonenumbers` rather than this pattern,
    so a number dressed as a decimal would fail loudly rather than be published quietly."""
    assert not is_structural("+91 98765 43210")
    assert not is_structural("0124 456 7890")
    assert is_structural("22.845769")


def test_a_visual_separator_does_not_split_a_number() -> None:
    r"""One clinic's footer prints its number as `0124 · 519 9464`, with a middle dot.

    `\s` does not match U+00B7, so the number split into two runs too short to match and the
    scrubber replaced only half of it, leaving real digits in a published file. The bullet, the
    Unicode dashes and U+FFFD are in the separator class for the same reason. (I first read the
    character as U+FFFD off a cp1252 console; it is a middle dot, and the comment in `scrub.py`
    now says so.)
    """
    html = "<p>Call 0124 " + chr(0x00B7) + " 519 9464 today</p>"
    [phone] = scrub(html, slug="p").substitutions
    assert "0124" not in phone.replacement or phone.replacement.count("0124") == 1
    assert "519 9464" not in scrub(html, slug="p").html, "the real digits must be gone"


def test_a_year_range_and_a_dotted_date_are_structure() -> None:
    """`Copyright © 2019-2024` became `4159-6201` and a news list's `12.03.2024` became
    `01.60.6402`. Neither is anybody's contact detail, and mangling them corrupts the page."""
    for value in ("2019-2024", "2012 - 2023", "12.03.2024", "1.418.793"):
        assert is_structural(value), value
        assert scrub(f"<p>{value}</p>", slug="p").substitutions == [], value


def test_a_real_number_is_still_not_structure() -> None:
    """The guard list has grown to six shapes, so this is worth asserting as it grows."""
    for value in ("+91 98765 43210", "0124 456 7890", "011 4567 8900", "1800 123 4567"):
        assert not is_structural(value), value
        assert scrub(f"<p>{value}</p>", slug="p").substitutions, value


def test_an_asset_filename_is_not_an_address() -> None:
    """A retina asset is named `logo@2x.webp`, and Webflow serves it as `logo%402x.webp` --
    which the email pattern matches, because `%40` is one of the spellings of `@` it exists to
    catch. The substitution rewrote an asset URL into a mailbox, and because the label recorded
    the `@` spelling while the page kept `%40`, the value then fell out of the labels entirely.
    """
    html = '<img src="https://cdn.example/a/logo%402x.webp"><img src="https://c.example/b@2x.png">'
    result = scrub(html, slug="p")
    assert result.substitutions == []
    assert "logo%402x.webp" in result.html
    assert "b@2x.png" in result.html


def test_a_real_address_at_a_real_domain_is_still_substituted() -> None:
    """The guard is on the suffix, so an address is still an address."""
    [email] = scrub('<a href="mailto:info@clinic.in">x</a>', slug="p").substitutions
    assert email.kind == "email"
