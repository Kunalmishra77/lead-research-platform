"""Phone normalisation (docs/06 section 6, task 3.12).

The rule used to exist twice: `phonenumbers` against the company's country in the crawl, and a
hand-rolled "must start with +" in storage. The second dropped 481 real numbers on the live
graph -- 43% of the companies that had a phone had no dialable one -- so what is pinned here is
the behaviour that rescued them, and the behaviour that must not change with it.
"""

import phonenumbers
import pytest

from app.normalize.phones import line_type, parse, to_e164


def test_a_national_number_needs_its_country_to_be_read() -> None:
    """The whole point. `085277 87137` is a real Indian mobile and nothing at all without IN.

    This is the case storage was dropping: 481 of them, every one a business someone could have
    rung. The `0` is a trunk prefix, and knowing to remove it rather than treat it as a digit is
    exactly the country-specific rule that cannot be hand-rolled.
    """
    assert to_e164("085277 87137", "IN") == "+918527787137"
    assert to_e164("085277 87137") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+918860692858", "+918860692858"),
        ("+91 88606 92858", "+918860692858"),
        ("+91-886-069-2858", "+918860692858"),
        ("  +918860692858  ", "+918860692858"),
    ],
)
def test_a_number_that_states_its_country_is_normalised_without_one(
    raw: str, expected: str
) -> None:
    """What the old rule got right, and what must keep working: of the 744 numbers already
    stored as E.164, `phonenumbers` calls none invalid, so nothing in the graph is lost."""
    assert to_e164(raw) == expected


def test_the_country_does_not_override_a_number_that_states_its_own() -> None:
    """A UK number on an Indian company's page is still a UK number."""
    assert to_e164("+44 20 7183 8750", "IN") == "+442071838750"


@pytest.mark.parametrize("raw", ["", None, "   ", "not a phone", "12", "0215 900 1060"])
def test_what_is_not_a_phone_number_is_refused(raw: str | None) -> None:
    """`0215 900 1060` is the one number from the live measurement that is not rescued, kept
    here as a case so a future looser rule has to admit it is letting something through."""
    assert to_e164(raw, "IN") is None


def test_a_pin_code_is_not_a_phone_number() -> None:
    """`is_valid_number`, not `is_possible_number`. Possible accepts any string of the right
    length, which is how PIN codes and years get in."""
    assert to_e164("110001", "IN") is None
    assert to_e164("2026", "IN") is None


def test_the_country_is_read_whatever_its_case() -> None:
    assert to_e164("085277 87137", "in") == to_e164("085277 87137", "IN")


def test_the_shape_the_column_accepts_is_the_last_word() -> None:
    """`company_locations.phone_e164` has its own CHECK and a CheckViolation fails the whole
    batch, so anything this returns has to satisfy it: a leading `+`, then 7 to 15 digits, the
    first of them not a zero."""
    value = to_e164("085277 87137", "IN")
    assert value is not None
    assert value.startswith("+")
    digits = value[1:]
    assert digits.isdigit()
    assert 7 <= len(digits) <= 15
    assert not digits.startswith("0")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("098685 49246", "mobile"),
        ("+919811881289", "mobile"),
        ("+91 11 2345 6789", "fixed_line"),
        ("1800 123 4567", "toll_free"),
        # A real answer, not a shrug: `phonenumbers` knows this range is one or the other and
        # will not guess. 16 rows in `field_values.phone_type` say exactly this, so a caller
        # deciding whether to send an SMS has to handle it rather than read it as "mobile".
        ("085277 87137", "fixed_line_or_mobile"),
    ],
)
def test_line_type_uses_the_words_already_stored(raw: str, expected: str) -> None:
    """182 rows in `field_values.phone_type` use these, written by `app/crawl/mapping.py`.
    Renaming them would make one line type read as two things depending on when it was
    collected.

    The expectations are what `phonenumbers` actually answers for these numbers, checked
    before they were written down -- not what an Indian 85xxxxxxxx number looks like it
    should be.
    """
    number = parse(raw, "IN")
    assert number is not None
    assert line_type(number) == expected


def test_a_range_nobody_classifies_is_unknown_rather_than_a_guess() -> None:
    number = phonenumbers.PhoneNumber()
    number.country_code = 1
    number.national_number = 5555555555
    assert line_type(number) in {"unknown", "fixed_line_or_mobile", "voip", "mobile"}


def test_parse_and_to_e164_agree_on_what_is_a_number() -> None:
    """Two entry points, one decision -- the thing that went wrong when there were two modules.

    `parse` is for a caller that needs the line type too; `to_e164` is for the column. They must
    never disagree about whether a number is real.
    """
    for raw in ["085277 87137", "+918860692858", "not a phone", "110001", "0215 900 1060"]:
        assert (parse(raw, "IN") is None) == (to_e164(raw, "IN") is None), raw
