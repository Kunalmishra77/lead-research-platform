"""Deciding whether a search result is the business's own site (ADR-0006).

Three rules, each one earned by watching the previous one fail on cases it was not designed from.

**Not "the first result that isn't a directory."** That picked `magicpin.in` and `kivihealth.com`
for two of five real businesses: the tail of listing sites has no end and no hand-written
exclusion list will ever contain it.

**Not "does a word of the name appear in the domain."** That scored `mosaic.in` a perfect 1.0 for
"Om Sai Clinic" (it contains "sai") and accepted `apple.com` for "Apple Dental Care".

**Not "how much of the name is in the domain, and how much of the domain is in the name."**
Nearer, and still wrong in both directions at once. It declined `dentalgalaxy.in` for
**"Dental Galaxy Pvt Ltd"** (0.50) and `harshaldental.com` for **"Dr. Harshal's Dental Clinic"**
(0.40) — honorifics and legal suffixes never appear in a domain but were still counted against it,
and those two shapes are most of the target market. Meanwhile it accepted `sunpharmacy.in` for
"Sun Pharma" (0.82) and `orthocareers.com` for "Ortho Care" (0.75), because a *substring* test
never asks what the leftover characters of the domain are.

So the rule now **segments**: the domain has to be readable, left to right, as name words in
order, with **nothing left over**. `sunpharma` reads as "sun"+"pharma"; `sunpharmacy` leaves "cy"
and is rejected outright. What the domain skips is free — "Pvt Ltd", "Clinic", "Dental Care" —
which is exactly what a real company drops when it registers a domain.

A wrong website is worse than none: it sends the crawler to another company and attributes their
phone number and address to this one, with provenance that looks entirely credible. So where the
rule is unsure it declines, and a miss is the cheaper mistake.

**What this cannot do.** Two businesses with the same name in different cities are indistinguishable
to any rule that only reads strings: "Sharma Dental Clinic" in Pune matches a Delhi practice's
`sharmadental.com` perfectly. The city is in the query, so the search engine ranks with it, but the
score does not know it. That is a resolution problem (docs/06), not a matching one, and it is why
the confidence this module yields stays below a value from any source that named the business.
"""

import re
import unicodedata
from functools import lru_cache
from urllib.parse import urlparse

#: Words that appear in half the business names in a category and so identify nothing on their
#: own. A domain is free to skip them, and using one does not make a match.
GENERIC_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "clinic",
        "centre",
        "center",
        "hospital",
        "dental",
        "dentistry",
        "care",
        "shop",
        "store",
        "services",
        "solutions",
        "studio",
        "salon",
        "cafe",
        "restaurant",
        "hotel",
        "school",
        "academy",
        "institute",
        "consultancy",
        "consultants",
        "enterprises",
    }
)

#: Titles and legal forms. These are dropped before anything is measured: no one puts "Pvt Ltd" in
#: a domain, and counting it against a candidate is what made "Dental Galaxy Pvt Ltd" decline its
#: own site. Unlike GENERIC_WORDS they are removed entirely, not merely discounted.
DROPPED_WORDS: frozenset[str] = frozenset(
    {
        "dr",
        "dr's",
        "mr",
        "mrs",
        "ms",
        "prof",
        "shri",
        "smt",
        "ltd",
        "limited",
        "pvt",
        "private",
        "inc",
        "incorporated",
        "llp",
        "llc",
        "co",
        "company",
        "corp",
        "corporation",
        "gmbh",
        "bv",
        "sa",
        "srl",
    }
)

#: Hosts that are never a business's own site, matched against **every** label of the host: the
#: first version compared only the leftmost one, so `in.linkedin.com` — the host LinkedIn serves to
#: Indian users, our primary market — stemmed to "in" and sailed through. Free site builders are
#: here for the same reason: `dentalgalaxy.justdial.com` scored a perfect 1.0 as a company website.
KNOWN_AGGREGATOR_STEMS: frozenset[str] = frozenset(
    {
        "facebook",
        "instagram",
        "linkedin",
        "twitter",
        "youtube",
        "google",
        "bing",
        "duckduckgo",
        "justdial",
        "indiamart",
        "practo",
        "sulekha",
        "yelp",
        "tripadvisor",
        "quikr",
        "magicpin",
        "kivihealth",
        "lybrate",
        "zomato",
        "swiggy",
        "amazon",
        "blogspot",
        "wordpress",
        "wixsite",
        "weebly",
        "business",
    }
)

#: Public suffixes that are two labels deep, so the registrable name is the third from the right.
#: Not the full Public Suffix List: this is the set our target geographies actually use, and an
#: unlisted one costs a conservative decline rather than a wrong answer.
MULTI_LABEL_SUFFIXES: frozenset[str] = frozenset(
    {
        "co.in",
        "net.in",
        "org.in",
        "gen.in",
        "firm.in",
        "ind.in",
        "ac.in",
        "co.uk",
        "org.uk",
        "me.uk",
        "com.au",
        "net.au",
        "com.sg",
        "com.my",
        "co.nz",
        "co.za",
        "com.br",
        "co.jp",
        "com.mx",
        "com.tr",
        "ae.org",
    }
)

#: Below this, the domain is not claimed. `resolve_website` returning nothing is a normal answer:
#: about half the businesses in the ADR-0006 test genuinely have no site of their own.
MIN_CONFIDENCE = 0.6

#: A word shorter than this cannot carry a match on its own, though it may still be consumed as
#: part of the domain ("a1dental" reads as "a"+"1"+"dental").
MIN_TOKEN_LENGTH = 3


def host_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.").split(":")[0]


def _fold(text: str) -> str:
    """Strips accents so "Café Müller" keeps its words instead of losing them to the regex."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", _fold(text).lower()) if w]


def core_words(name: str) -> list[str]:
    """The name with titles and legal forms removed, in order.

    Order matters now: the domain has to read as these words left to right, and "Dental Galaxy"
    registering `galaxydental.in` is a different (and much weaker) claim than `dentalgalaxy.in`.
    """
    return [w for w in _words(name) if w not in DROPPED_WORDS]


def name_tokens(name: str) -> set[str]:
    """The distinguishing words of a business name — what a domain has to use to mean anything.

    Falls back to every word when stripping the generic ones leaves nothing: "AO Dentistry" is all
    generic except two letters, and dropping it entirely was a real miss in the ADR-0006 test.
    """
    words = core_words(name)
    distinctive = {w for w in words if w not in GENERIC_WORDS and len(w) >= MIN_TOKEN_LENGTH}
    return distinctive or set(words)


def registrable_stem(url: str) -> str:
    """The name the owner registered, without the public suffix and without any subdomain.

    `dentalgalaxy.justdial.com` is Justdial's; `32smiles.co.in` is `32smiles`. The first version
    took the leftmost label instead, which got both of those exactly backwards.
    """
    host = host_of(url)
    labels = host.split(".")
    if len(labels) < 2:
        return host.replace("-", "")
    suffix_labels = 2 if ".".join(labels[-2:]) in MULTI_LABEL_SUFFIXES else 1
    if len(labels) <= suffix_labels:
        return ""
    return labels[-(suffix_labels + 1)].replace("-", "")


def is_aggregator(url: str) -> bool:
    """True if any label of the host is a known directory, social network or site builder."""
    return any(label in KNOWN_AGGREGATOR_STEMS for label in host_of(url).split("."))


@lru_cache(maxsize=2048)
def _segment(stem: str, words: tuple[str, ...]) -> tuple[str, ...] | None:
    """The name words that spell out `stem` exactly, in order, or None if they cannot.

    Words may be skipped — a domain drops what it likes — but every character of the stem must be
    claimed by one. That last part is the whole point: "sunpharmacy" leaves "cy" unclaimed, so
    "Sun Pharma" does not match it however well the prefix reads.

    Prefers the segmentation that uses the most words, so `dentalgalaxy` is read as two words
    rather than as some shorter accident.
    """
    if not stem:
        return ()
    best: tuple[str, ...] | None = None
    for index, word in enumerate(words):
        if word and stem.startswith(word):
            rest = _segment(stem[len(word) :], words[index + 1 :])
            if rest is not None and (best is None or 1 + len(rest) > len(best)):
                best = (word, *rest)
    return best


def _reading(name: str, url: str) -> tuple[list[str], str, tuple[str, ...]] | None:
    """The name's words, the domain they have to spell, and the ones that do — or None.

    None is every way a domain can fail to be this business's: it belongs to a directory, there
    is nothing to compare, or the words cannot account for every character of it.
    """
    if is_aggregator(url):
        return None
    stem = registrable_stem(url)
    words = core_words(name)
    if not stem or not words:
        return None
    used = _segment(stem, tuple(words))
    return None if used is None else (words, stem, used)


def domain_match(name: str, url: str) -> float:
    """How much a URL's domain looks like it belongs to this business, from 0 to 1.

    The domain must read as the business's words in order with nothing left over; the score is
    then how much of the **distinctive** part of the name those words cover. A domain that uses
    only one word of a longer name scores 0 unless it is the whole name, because "Apple Dental
    Care" has no claim on `apple.com` and "Smile Smiles" none on `smiles.com`.
    """
    reading = _reading(name, url)
    if reading is None:
        return 0.0
    words, stem, used = reading
    if "".join(words) == stem:
        # The domain is the name, nothing dropped. Short words count here, which is what keeps
        # "AO Dentistry" -> aodentistry.com and "32Smiles" -> 32smiles.co.in found.
        return 1.0
    if len(used) < 2:
        # One word out of several is a coincidence as often as a claim, and we cannot tell which.
        return 0.0
    distinctive = name_tokens(name)
    covered = distinctive & set(used)
    return round(len(covered) / len(distinctive), 3) if covered else 0.0


def homepage_of(url: str) -> str:
    """The site, not the page of it we happened to land on.

    A search often ranks an inner page first — `dentalgalaxy.in/our-team/` outranked the
    homepage in the live check. `website` is a fact about the business, so it is the origin;
    the crawler decides which pages of it to read (docs/06).
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    host = parsed.netloc.lower()
    return f"{parsed.scheme}://{host}/"


def best_website(name: str, candidates: list[str]) -> tuple[str | None, float]:
    """The candidate URL most likely to be this business's own site, and how sure we are.

    Returns `(None, score)` when nothing matches well enough, which is a normal answer: about
    half the businesses in the ADR-0006 test genuinely have no site of their own. Ties go to
    whichever the search engine ranked higher, rather than to whichever sorts last.
    """
    ranked = sorted(
        ((domain_match(name, url), -position, url) for position, url in enumerate(candidates)),
        reverse=True,
    )
    if not ranked:
        return None, 0.0
    score, _, url = ranked[0]
    return (homepage_of(url), score) if score >= MIN_CONFIDENCE else (None, score)
