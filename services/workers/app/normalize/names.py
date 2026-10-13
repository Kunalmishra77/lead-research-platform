"""Company names (docs/06 section 6, ADR-0018): the key resolution blocks on.

`companies.normalized_name` is NOT NULL and is what later resolution blocks on. It used to trim,
casefold and fold punctuation to spaces, which is most of the work and misses the case that
actually breaks matching.

Measured on the live graph (1,550 companies) before this module existed: 11 keys contained a
non-ASCII character, and the worst of them were not accents. Two dental clinics had written their
listing in MATHEMATICAL BOLD and MATHEMATICAL SANS-SERIF BOLD capitals -- `\U0001d402\U0001d403`
where a reader sees "CD" -- which are letters to `str.isalnum()` and so survived into the key
verbatim. That key could never match the same business written in plain letters, by another
source, by a crawl of its own site, or by a customer's import.

(The characters are written as escapes here on purpose: ruff's RUF002 refuses the literals, for
exactly the reason this module exists.)

Two things follow, and ADR-0018 has the reasoning:

* The key is transliterated to ASCII with `anyascii` (ISC), not `unidecode` (GPL), which docs/06
  used to name. The two do the same work and only one of them is a question somebody has to
  answer when this ships.
* Legal suffixes are stripped although only 8 of 1,550 names carry one and stripping merges
  nothing today. A customer importing their own CRM export will carry far more, and "Acme
  Industries Pvt Ltd" and "Acme Industries" are one company.

Transliteration is a claim about pronunciation, not a translation: `सत्यम` becomes `satyam`. That
belongs in a matching key and must never be shown as a business's name. `canonical_name` keeps
what the source said, and that is what the UI reads.
"""

import re
from typing import Final

from anyascii import anyascii

#: Suffixes that say what a company is registered as rather than what it is called. Ordered
#: longest-first at use, so `pvt ltd` is taken before `ltd` leaves `pvt` behind.
#:
#: Written without punctuation because they are matched after it has been folded to spaces:
#: `Pvt. Ltd.`, `Pvt Ltd` and `PVT. LTD` all arrive here as `pvt ltd`.
LEGAL_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "pvt ltd",
        "pvt limited",
        "private ltd",
        "private limited",
        "p ltd",
        "ltd",
        "limited",
        "llp",
        "llc",
        "inc",
        "incorporated",
        "gmbh",
        "bv",
        "nv",
        "sa",
        "ag",
        "plc",
        "corp",
        "corporation",
    }
)

_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:\s+(?:" + "|".join(sorted(map(re.escape, LEGAL_SUFFIXES), key=len, reverse=True)) + r"))+$"
)


def matching_key(name: str) -> str:
    """The blocking key for a company name: folded to ASCII, punctuation to spaces, no suffix.

    Deliberately not unique and not an identity. `primary_domain` is the dedupe handle (ADR-0007)
    and this only proposes candidate pairs for scoring, so folding two spellings together costs
    comparisons where failing to fold them loses the match altogether.

    Never empty for a name with any content: a name written entirely in another script
    transliterates rather than vanishing, because the column is NOT NULL and an empty key blocks
    against every other empty key.
    """
    folded = anyascii(name).casefold()
    spaced = " ".join("".join(c if c.isalnum() else " " for c in folded).split())
    if not spaced:
        return spaced
    # The suffix comes off only if something is left. "Ltd" alone is a name we cannot improve on,
    # and an empty key would block against every other empty key.
    without = _SUFFIX_RE.sub("", spaced).strip()
    return without or spaced
