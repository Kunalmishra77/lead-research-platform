"""The corpus contains nothing real (task 3.14, ADR-0020).

The repository is public. 14 of 22 surveyed homepages carried a named individual's address, so a
scrubber that misses one value publishes somebody's email -- and the miss is invisible: the
fixture looks fine, every other test passes, and the address is on GitHub.

So this asks one question of every contact-shaped string in every committed fixture: is it a
value the scrubber put there? Anything else is unaccounted for, and unaccounted for is treated as
real until somebody proves otherwise.

It runs the scrubber's own patterns, which are deliberately broader than the extractors'. That is
the point: a value the extractors cannot see can still be somebody's phone number.
"""

import json
import re
from typing import Final
from urllib.parse import unquote

from app.golden.corpus import CORPUS
from app.golden.scrub import _EMAIL, _ISO_DATE, _NUMERIC_TLD, _PHONE
from app.normalize.phones import to_e164


def _accounted_for() -> set[str]:
    """Every value any label mentions, in all three categories.

    All three, because a leak detector that cries wolf is the first thing anyone learns to
    ignore -- this one did, on the first labelled site, by not knowing about `also_theirs`.
    """
    known: set[str] = set()
    for labels in CORPUS.glob("*/labels.json"):
        raw = json.loads(labels.read_text(encoding="utf-8"))
        known.update(str(v).lower() for v in (raw.get("expect") or {}).values())
        known.update(str(v).lower() for v in (raw.get("absent") or []))
        known.update(str(v).lower() for v in (raw.get("also_theirs") or []))
    return known


def _digits(value: str) -> str:
    return "".join(c for c in value if c.isdigit())


#: Deliberately *not* `_PHONE`. The gate shared the scrubber's pattern, so it shared its blind
#: spots: eleven real numbers sat in three committed fixtures because `tel:099103%2008147` splits
#: around the `%` into two runs too short to match, and because the span `{6,16}` was one short of
#: `9-2-1-7-8-3-6-5-2-1`. A detector built from the thing it is checking cannot find that.
#:
#: So this one is wider, decodes percent-escapes first, and asks `phonenumbers` the only question
#: that matters for a published file: can somebody dial this?
_DIALABLE: Final[re.Pattern[str]] = re.compile(r"(?<![\w.])(\+?\d[\d\s\-().]{6,28}\d)(?!\d)")


def test_no_fixture_holds_a_dialable_number_no_label_accounts_for() -> None:
    """The strongest form of the question, and the one that failed when it was first written.

    `_accounted_for` is matched on digits, with and without a leading zero or country code,
    because a label records what the page prints and this reads what a phone would dial.
    """
    known: set[str] = set()
    for value in _accounted_for():
        digits = _digits(value)
        if digits:
            known.add(digits)
            known.add(digits.lstrip("0"))
            known.add(digits.removeprefix("91"))

    leaks: list[str] = []
    for fixture in sorted(CORPUS.glob("*/*.html")):
        # Decoded, because an escape is not a disguise: `%20` is a space to every browser and to
        # `app/crawl/contacts.py`, so it has to be one here too.
        html = unquote(fixture.read_text(encoding="utf-8"))
        for match in _DIALABLE.finditer(html):
            printed = match.group(1).strip()
            digits = _digits(printed)
            if digits in known or digits.lstrip("0") in known or digits.removeprefix("91") in known:
                continue
            if to_e164(printed, "IN") is None:
                continue
            leaks.append(f"{fixture.parent.name}: {printed!r}")
    assert leaks == [], f"dialable numbers no label accounts for: {leaks}"


def test_every_address_in_the_corpus_is_one_the_scrubber_wrote() -> None:
    known = _accounted_for()
    unaccounted: list[str] = []
    for fixture in sorted(CORPUS.glob("*/*.html")):
        html = fixture.read_text(encoding="utf-8")
        for match in _EMAIL.finditer(html):
            flat = re.sub(r"\s", "", f"{match.group(1)}@{match.group(3)}").lower()
            flat = re.sub(r"\(dot\)|\[dot\]", ".", flat)
            if _NUMERIC_TLD.search(flat):
                # What the scrubber deliberately leaves: `bootstrap@5.3.8` in a CDN URL. The
                # same constant it uses, so the exemption here cannot drift wider than the
                # one there -- and a numeric TLD is not a thing that exists.
                continue
            if flat not in known:
                unaccounted.append(f"{fixture.parent.name}: {flat}")
    assert unaccounted == [], f"addresses no label accounts for: {unaccounted}"


def test_every_number_in_the_corpus_is_one_the_scrubber_wrote() -> None:
    known_digits = {_digits(value) for value in _accounted_for() if _digits(value)}
    unaccounted: list[str] = []
    for fixture in sorted(CORPUS.glob("*/*.html")):
        html = fixture.read_text(encoding="utf-8")
        for match in _PHONE.finditer(html):
            digits = _digits(match.group(1))
            if not 7 <= len(digits) <= 15:
                continue
            if _ISO_DATE.match(match.group(1).strip()):
                # A date the scrubber now leaves on the page. Exempted with its own constant so
                # the gate and the scrubber agree by construction: `2024-03-12` was being
                # replaced by `3644-44-80`, which published nothing but corrupted the fixture.
                continue
            if digits not in known_digits:
                unaccounted.append(f"{fixture.parent.name}: {match.group(1)!r}")
    assert unaccounted == [], f"numbers no label accounts for: {unaccounted[:10]}"


def test_no_fixture_keeps_a_stylesheet_or_a_script_it_should_not() -> None:
    """The reducer's work, checked on the committed result rather than on a unit input.

    A site's stylesheet and its scripts are its owner's code, and they were also 10 of the first
    harvest's 12 MB. JSON-LD is the one script kept, because the extractors read it.
    """
    for fixture in sorted(CORPUS.glob("*/*.html")):
        html = fixture.read_text(encoding="utf-8")
        assert "<style" not in html.lower(), fixture
        assert "<svg" not in html.lower(), fixture
        assert "base64," not in html, fixture
        for match in re.finditer(r"(?is)<script\b[^>]*>", html):
            assert "application/ld+json" in match.group(0).lower(), (
                f"{fixture}: a script that is not JSON-LD survived"
            )


def test_no_fixture_names_the_site_it_came_from() -> None:
    """The fixture's own slug is in its synthetic hosts, which is deliberate -- a failure
    message has to be recognisable. What must not survive is the real registrable domain, and
    the slug is derived from it, so this checks the one thing that distinguishes them: a real
    domain appears in a URL the corpus did not rewrite.
    """
    for labels_path in sorted(CORPUS.glob("*/labels.json")):
        raw = json.loads(labels_path.read_text(encoding="utf-8"))
        for page in raw.get("pages", []):
            assert "example.in" in str(page["url"]), (
                f"{labels_path.parent.name}: {page['url']} still names a real host"
            )
