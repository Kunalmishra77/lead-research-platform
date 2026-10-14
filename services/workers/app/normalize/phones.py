"""Phone numbers (docs/06 section 6): one rule, in one place.

There were two. `app/crawl/contacts.py` parsed with `phonenumbers` against the company's
country, which is the rule the spec states. `app/db/batch.py` had its own: accept a number that
already begins with `+`, strip the separators, and refuse everything else -- because a national
number cannot be normalised without knowing the country, and inventing a country code would put
a real call through to the wrong country.

That reasoning is sound and the conclusion was wrong: the country is known. It is on the
candidate, and `phonenumbers` has the per-country rules for the trunk prefix. Measured on the
live graph on 2026-10-12, before this module existed:

    companies with a phone value            1,312
      ... also stored as phone_e164           745   57%
      ... phone known but not dialable        567   43%

Of the 567, 481 were national-format numbers like `085277 87137` -- real Indian mobiles that
`batch.py` dropped for want of a `+`. Checked before changing anything: `phonenumbers` normalises
473 of the 474 distinct ones (the hold-out is `0215 900 1060`), and of the 744 numbers already
stored as E.164 it calls **none** invalid. So this rescues the 473 and loses nothing.

The remaining 86 are a different defect and not this module's: they came from the crawl, which
writes a `phone` field value and never updates `company_locations.phone_e164`. That is what task
3.13 (best value per company after each batch) is for.
"""

from typing import Final

import phonenumbers

#: What `phonenumbers` calls a line, in the vocabulary already stored in `field_values.phone_type`
#: (written by `app/crawl/mapping.py`). Kept as it was rather than tidied: 182 rows use these
#: four, and renaming them would make the same line type read as two different things depending
#: on when it was collected.
_LINE_TYPES: Final[dict[int, str]] = {
    phonenumbers.PhoneNumberType.MOBILE: "mobile",
    phonenumbers.PhoneNumberType.FIXED_LINE: "fixed_line",
    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_line_or_mobile",
    phonenumbers.PhoneNumberType.TOLL_FREE: "toll_free",
    phonenumbers.PhoneNumberType.VOIP: "voip",
}

#: `company_locations.phone_e164` is CHECKed against this shape. A number `phonenumbers` calls
#: valid should always satisfy it, and the guard stays anyway: a CheckViolation fails the whole
#: batch, so the last word on what goes in that column belongs next to the column's own rule.
#: Characters a page uses to space a number out, folded to a plain space before parsing.
#: `phonenumbers` knows the ASCII punctuation and nothing else, so a clinic whose footer prints
#: `0124 U+00B7 519 9464` was unreadable to the whole pipeline -- `0124 519 9464` is a valid
#: Gurugram landline and the middle dot alone was enough to lose it. The golden corpus found it
#: (task 3.14): the scrubber matched the number, `to_e164` refused it, and the label had nowhere
#: to go. `app/crawl/contacts.py` already folds the Unicode dashes for the same reason; this is
#: one level down, where every source passes through.
#:
#: Spelled as escapes, because a file that prints a non-breaking space as a space is a file
#: nobody can review -- which is what ruff's ambiguous-character rule is for.
_VISUAL_SEPARATORS: Final[dict[int, str]] = str.maketrans(
    dict.fromkeys(
        "\u00a0\u00b7\u2022\u2027\u2010\u2011\u2012\u2013\u2014\u2015",
        " ",
    )
)

_E164_MIN_DIGITS: Final[int] = 7
_E164_MAX_DIGITS: Final[int] = 15


def parse(raw: str | None, region: str | None = None) -> phonenumbers.PhoneNumber | None:
    """A number `phonenumbers` accepts for a real region, or None.

    `region` is the company's country (ISO-3166 alpha-2) and decides how a local number without
    a country code is read. Without it only numbers written with `+` survive, which is the
    honest outcome: `98765 43210` is a valid mobile in India and nothing at all without knowing
    that it is India.

    `is_valid_number`, not `is_possible_number`: possible accepts any string of the right
    length, which is how PIN codes and years get in.
    """
    if not raw:
        return None
    try:
        cleaned = raw.translate(_VISUAL_SEPARATORS)
        parsed = phonenumbers.parse(cleaned, (region or "").upper() or None)
    except phonenumbers.NumberParseException:
        return None
    return parsed if phonenumbers.is_valid_number(parsed) else None


def to_e164(raw: str | None, region: str | None = None) -> str | None:
    """The number in the one shape `company_locations.phone_e164` accepts, or None."""
    parsed = parse(raw, region)
    if parsed is None:
        return None
    value = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    digits = value[1:]
    if not _E164_MIN_DIGITS <= len(digits) <= _E164_MAX_DIGITS or digits.startswith("0"):
        return None
    return value


def line_type(parsed: phonenumbers.PhoneNumber) -> str:
    """mobile, fixed_line, toll_free and so on -- `unknown` when the range does not say."""
    return _LINE_TYPES.get(phonenumbers.number_type(parsed), "unknown")
