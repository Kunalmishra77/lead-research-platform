"""City names (docs/06 section 6).

The module only tidies; the seeded geography does the matching. So what is pinned here is the
tidying, and in particular the two ways it could do harm: taking a word that is part of a real
name, and leaving an administrative suffix on so the lookup finds nothing.
"""

import pytest

from app.normalize.cities import ADMIN_SUFFIXES, lookup_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Every one of these is in the live graph.
        ("Gurugram Sub-District", "Gurugram"),
        ("Bahadurgarh Sub-District", "Bahadurgarh"),
        ("Pataudi Sub-District", "Pataudi"),
        ("Gurugram", "Gurugram"),
        ("Gurgaon", "Gurgaon"),
    ],
)
def test_an_administrative_suffix_comes_off_before_the_lookup(raw: str, expected: str) -> None:
    """A census extract writes "Gurugram Sub-District" and no alias list will ever carry it.

    `Gurgaon` is returned untouched on purpose: deciding here that it means Gurugram would be a
    second, smaller copy of the geography. `app.geo_areas` holds that alias.
    """
    assert lookup_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Thane Sub District", "Thane"),
        ("Thane sub-district", "Thane"),
        ("Thane SUB-DISTRICT", "Thane"),
        ("Faridabad District", "Faridabad"),
        ("Pune Municipal Corporation", "Pune"),
        ("Nashik Urban Agglomeration", "Nashik"),
        ("Kalyan Tehsil", "Kalyan"),
    ],
)
def test_the_suffix_is_recognised_however_it_is_joined_or_cased(raw: str, expected: str) -> None:
    assert lookup_name(raw) == expected


def test_sub_district_is_taken_whole_rather_than_leaving_sub_behind() -> None:
    """The bug this caught in its own first draft.

    `re.escape("-")` yields a backslash-hyphen, so substituting the hyphen afterwards corrupted
    the escape, `district` matched on its own, and "Gurugram Sub-District" became "Gurugram
    Sub" -- a city name nobody has ever used, which would then match nothing and be stored as a
    fact.
    """
    assert lookup_name("Gurugram Sub-District") == "Gurugram"
    assert "sub" not in (lookup_name("Gurugram Sub-District") or "").lower()


@pytest.mark.parametrize("raw", ["Jersey City", "Salt Lake City", "Quezon City", "Cape Town"])
def test_a_word_that_is_part_of_a_real_name_is_not_taken(raw: str) -> None:
    """`city` and `town` are deliberately not in the list. Taking them turns a name into a
    different one, and the result would be looked up and possibly found."""
    assert lookup_name(raw) == raw


def test_whitespace_is_collapsed_but_nothing_else_about_the_name_changes() -> None:
    """Two of the live rows are `New delhi` and `new delhi`. Case is not corrected here: the
    geography's alias matching is case-insensitive, and lower-casing the stored column would
    show the customer a city name in the wrong case."""
    assert lookup_name("  New   Delhi  ") == "New Delhi"
    assert lookup_name("New delhi") == "New delhi"


@pytest.mark.parametrize("raw", [None, "", "   ", ",", "-"])
def test_nothing_to_ask_about_is_nothing(raw: str | None) -> None:
    assert lookup_name(raw) in (None, raw)


def test_a_name_that_is_only_a_suffix_survives() -> None:
    """There is a Block in Karnataka and a District in several places. Returning an empty string
    would send `find_area("")` looking for every city at once."""
    assert lookup_name("District") == "District"
    assert lookup_name("Block") == "Block"


def test_every_suffix_is_lowercase_and_has_no_punctuation_but_hyphens() -> None:
    """The patterns are built from these by splitting on spaces and hyphens, so an entry with a
    dot or a capital would silently never match."""
    for suffix in ADMIN_SUFFIXES:
        assert suffix == suffix.lower(), suffix
        assert all(c.isalpha() or c in " -" for c in suffix), suffix
        assert suffix.strip() == suffix, suffix
