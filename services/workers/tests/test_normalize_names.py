"""The company matching key (docs/06 section 6, ADR-0018).

`companies.normalized_name` is what resolution blocks on. What is pinned here is the property
that made this worth changing: the same business, written two ways, has to produce one key.

The decorative-Unicode cases are written as escapes rather than literals because ruff's RUF002
refuses the literals -- for exactly the reason this module exists.
"""

import pytest

from app.normalize.names import LEGAL_SUFFIXES, matching_key

#: "CDC - Cosmodontist Dental", as one Gurgaon clinic actually writes it: MATHEMATICAL BOLD
#: capitals and small letters, which `str.isalnum()` calls letters.
BOLD_CDC = "\U0001d402\U0001d403\U0001d402 - \U0001d402\U0001d428\U0001d42c\U0001d426\U0001d428"

#: "HOLISTIC DENTAL", in MATHEMATICAL SANS-SERIF BOLD capitals.
BOLD_HOLISTIC = (
    "\U0001d5db\U0001d5e2\U0001d5df\U0001d5dc\U0001d5e6\U0001d5e7\U0001d5dc\U0001d5d6"
    " \U0001d5d7\U0001d5d8\U0001d5e1\U0001d5e7\U0001d5d4\U0001d5df - DR ANU RATHI"
)


def test_decorative_unicode_letters_fold_to_the_letters_they_depict() -> None:
    """The case that made this worth doing, from the live graph.

    These are letters as far as `str.isalnum()` is concerned, so the old key kept them verbatim
    and could never match the same clinic written in plain capitals -- not by another source,
    not by a crawl of its own site, not by a customer's import.
    """
    assert matching_key(BOLD_CDC) == "cdc cosmo"
    assert matching_key(BOLD_HOLISTIC) == "holistic dental dr anu rathi"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Caf\xe9 M\xfcller", "cafe muller"),
        ("BOŁT Fitness", "bolt fitness"),  # no NFKD decomposition; needs transliteration
        ("j\xfcSTa Gurgaon", "justa gurgaon"),
        ("Viśokam - Yog, Food & Wellness", "visokam yog food wellness"),
    ],
)
def test_accents_and_letters_outside_ascii_fold_away(raw: str, expected: str) -> None:
    assert matching_key(raw) == expected


def test_a_name_in_another_script_transliterates_rather_than_vanishing() -> None:
    """The column is NOT NULL and an empty key blocks against every other empty key.

    Stripping non-ASCII would have been free and wrong: a Devanagari-only listing would end up
    with nothing to match on at all.
    """
    key = matching_key("मेकहाना")  # मेकहाना
    assert key
    assert key.isascii()


def test_a_bilingual_name_keeps_the_half_that_was_already_latin() -> None:
    """A listing written "Satyam Hospital - सत्यम हॉस्पिटल" has to keep blocking against a plain
    "Satyam Hospital". The transliterated half is imperfect and that is fine: this is a blocking
    key, so an extra token costs a comparison where losing "satyam hospital" loses the match."""
    key = matching_key("Satyam Hospital - सत्यम")
    assert key.startswith("satyam hospital")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Dr. Sanap's Clinic", "dr sanap s clinic"),
        ("  BLUE   Tokai  ", "blue tokai"),
        ("V-FITNESS GYM", "v fitness gym"),
        ("Shape Boxx  The Gym", "shape boxx the gym"),
    ],
)
def test_punctuation_and_spacing_do_not_make_a_second_business(raw: str, expected: str) -> None:
    assert matching_key(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme Industries Pvt. Ltd.", "acme industries"),
        ("Acme Industries Pvt Ltd", "acme industries"),
        ("Acme Industries Private Limited", "acme industries"),
        ("TRINORVA ASCENDANT LLP", "trinorva ascendant"),
        ("Artemis Medicare Services Ltd", "artemis medicare services"),
        ("Ozoxy India Pvt. Ltd.", "ozoxy india"),
    ],
)
def test_a_legal_suffix_is_not_part_of_the_key(raw: str, expected: str) -> None:
    """Merges nothing on today's graph: 8 of 1,550 names carry a suffix and no two are the same
    business. It is what makes a customer's own CRM export line up, where the suffix is written
    far more often than on a map listing."""
    assert matching_key(raw) == expected


def test_nested_suffixes_come_off_together() -> None:
    """`pvt ltd` has to be taken before `ltd`, or `pvt` is left behind as part of the name."""
    assert matching_key("Spine Infratech Pvt Ltd") == "spine infratech"
    assert matching_key("Spine Infratech Private Limited") == "spine infratech"


def test_a_name_that_is_only_a_suffix_keeps_it() -> None:
    """Nothing left over is worse than a suffix: the column is NOT NULL, and an empty key blocks
    against every other empty key -- which is every company whose name we failed to read."""
    assert matching_key("Ltd") == "ltd"
    assert matching_key("LLP") == "llp"


@pytest.mark.parametrize("raw", ["", "   ", "!!!", "---"])
def test_a_name_with_no_letters_yields_an_empty_key_rather_than_an_error(raw: str) -> None:
    """`canonical_name` is NOT NULL but nothing stops it being punctuation. An exception here
    would fail a whole batch of a hundred candidates for one bad row."""
    assert matching_key(raw) == ""


def test_every_suffix_is_written_the_way_the_key_will_see_it() -> None:
    """The suffixes are matched after punctuation has been folded to spaces, so an entry
    containing a dot or a capital would silently never match."""
    for suffix in LEGAL_SUFFIXES:
        assert suffix == suffix.lower(), suffix
        assert all(c.isalnum() or c == " " for c in suffix), suffix
        assert suffix.strip() == suffix, suffix
        assert "  " not in suffix, suffix
