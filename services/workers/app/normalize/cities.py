"""City names (docs/06 section 6): the spelling the columns are queried by.

Measured on the live graph on 2026-10-13, 1,550 companies:

    Gurugram                  955 |  New Delhi   288
    Gurgaon                     9 |  Delhi       128
    Gurugram Sub-District       6 |  New delhi     1
                                  |  new delhi     1

One city in three spellings and another in four. A customer filtering on "Gurgaon" was shown 9
of 970 businesses, and one filtering on "Delhi" was shown 128 of 418. Nothing about that is
visible in the result: every row returned is a real business in the right place.

The seeded geography already holds the answer -- `app.geo_areas` has Gurugram with the alias
Gurgaon, and the city Delhi with the alias New Delhi -- so this is not a judgement about which
spelling wins. It is a lookup that was never done. What this module adds is only the tidying a
lookup needs: a census extract writes "Gurugram Sub-District", which no alias list will ever
carry.

**The column is canonicalised; the field value is not.** `field_values` records what a source
said, with provenance, and rewriting "New Delhi" to "Delhi" there would misquote it. The columns
(`companies.city`, `company_locations.city`) are the working copy that filters and grouping read,
and those get the canonical spelling.
"""

import re
from typing import Final

#: Administrative words that a census or cadastral extract appends to a city's name. Stripped
#: before the geography is asked, because an alias list holds the names people use.
#:
#: Ordered longest-first at use, so "Sub-District" is taken before "District" can match inside
#: it and leave "Sub-" behind.
ADMIN_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "sub district",
        "sub-district",
        "subdistrict",
        "district",
        "tehsil",
        "taluk",
        "taluka",
        "mandal",
        "block",
        "municipal corporation",
        "municipality",
        "nagar palika",
        "nagar panchayat",
        "urban agglomeration",
        # Deliberately not "city" or "town". Those are parts of real names -- Jersey City, Salt
        # Lake City, Quezon City -- and taking them off turns a name into a different one. The
        # words above are what an administrative extract appends; these are what people say.
    }
)


def _pattern_for(suffix: str) -> str:
    r"""One suffix as a pattern whose words may be joined by a space, a hyphen or both.

    Built from the words rather than by escaping and substituting: `re.escape("-")` yields
    `\-`, so replacing `-` afterwards corrupts the escape and the pattern then matches a
    literal bracket. That exact mistake turned "Gurugram Sub-District" into "Gurugram Sub",
    which is a city name nobody has ever used.
    """
    return r"[\s-]+".join(re.escape(word) for word in suffix.replace("-", " ").split())


#: Longest first, so "sub district" is taken whole before "district" can match inside it.
_ALTERNATIVES: Final[str] = "|".join(
    sorted((_pattern_for(s) for s in ADMIN_SUFFIXES), key=len, reverse=True)
)

_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:[\s,-]+(?:" + _ALTERNATIVES + r"))+$", re.IGNORECASE
)


def lookup_name(city: str | None) -> str | None:
    """The city as the geography should be asked about it, or None when there is nothing to ask.

    Only tidying, never guessing: whitespace collapsed and an administrative suffix removed. The
    result is handed to `ReferenceData.find_area`, which does the alias matching -- deciding here
    that "Gurgaon" means Gurugram would be a second, smaller copy of the geography.
    """
    if not city:
        return None
    collapsed = " ".join(city.split())
    if not collapsed:
        return None
    without = _SUFFIX_RE.sub("", collapsed).strip(" ,-")
    # "City" is a suffix and also a whole name ("Jersey City", and in India "Howrah"): if taking
    # it leaves nothing, the word was the name.
    return without or collapsed
