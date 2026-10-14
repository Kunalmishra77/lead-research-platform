"""Turning a real page into a corpus fixture (task 3.14, ADR-0020).

Three jobs, in this order, and the order is the lesson:

1. **Reduce.** Strip the stylesheets, the scripts (except JSON-LD), the inline SVG and the base64
   images. Nothing that reads contact details reads any of it, it is most of a modern page's
   bytes, and it is the site owner's code rather than their markup. Skipping this step produced a
   12 MB corpus and 260 "contact values" per page -- every one a CSS length, an SVG coordinate or
   a date inside inline JavaScript -- which made the labels unreviewable. A reviewer who cannot
   read the labels does not review them.
2. **Substitute every contact value** with a synthetic one of the same shape. This is what makes
   the fixture publishable -- the repository is public and 14 of 22 surveyed homepages carried a
   named individual's address -- and it is also what produces the label: the expectation that the
   extractor finds `info@clinic-11.in` on this page comes from having put it there, not from
   running the extractor.
3. **Drop the prose.** A page's paragraphs are its owner's writing and no contact extractor
   reads them.

The matching is deliberately broader than the extractors'. A value the pipeline misses still
gets substituted, so it is still in the fixture and still in the labels, and the report then
shows it as a miss. A scrubber that replaced only what the pipeline found would be both a
privacy leak and blind in exactly the places worth measuring.

Shape is preserved because shape is what the extractors read. `097111 74449` becomes
`093433 59578` rather than `+91 93433 59578`: the national format is the thing
`app/normalize/phones.py` exists for, and a corpus that normalised it away would stop testing
the defect that dropped 481 numbers. Likewise a `mailto:` stays a link, an obfuscated `(at)`
stays obfuscated, a landline keeps its area code, and a domain keeps its real top-level domain --
`.test` would send every fixture down a path no real page takes (ADR-0020).
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import unquote

from app.normalize.phones import line_type, parse

#: Deliberately broader than `app/crawl/contacts.py`: this has to catch what the extractors
#: miss. It accepts any `@` between plausible local and domain parts, including the obfuscated
#: spellings, and leaves the decision about what it found to the labelling step.
_EMAIL: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b([A-Za-z0-9._%+\-]{1,64})"
    r"(\s*(?:@|\(\s*at\s*\)|\[\s*at\s*\]|\{\s*at\s*\}|&#0*64;|&commat;|%40)\s*)"
    r"([A-Za-z0-9](?:[A-Za-z0-9\-]{0,62}[A-Za-z0-9])?"
    r"(?:\s*(?:\.|\(\s*dot\s*\)|\[\s*dot\s*\])\s*"
    r"[A-Za-z0-9](?:[A-Za-z0-9\-]{0,62}[A-Za-z0-9])?)+)"
)

#: A `name@version` out of a CDN URL -- `cdn.jsdelivr.net/npm/bootstrap@5.3.8` -- which `_EMAIL`
#: reads as an address with the domain `5.3.8`. Skipped rather than substituted, because a TLD is
#: never all digits (ICANN forbids it, and IDNA could not encode it), so this cannot drop a real
#: address; and leaving the URL intact is better for the fixture than replacing a library version
#: with a mailbox.
_NUMERIC_TLD: Final[re.Pattern[str]] = re.compile(r"\.[0-9]+$")

#: A file extension where `_EMAIL` otherwise reads a domain. Retina assets are named
#: `logo@2x.webp`, and Webflow serves them percent-encoded as `logo%402x.webp` -- which this
#: pattern matches, because `%40` is one of the spellings of `@` it exists to catch. The
#: substitution then rewrote an asset URL into a mailbox, and the label recorded the `@` spelling
#: while the page held `%40`, so `occurrences` could never find it again and the value fell out of
#: the labels entirely. The leak gate caught that, as it should: from its side a value in no
#: category is indistinguishable from an address nobody scrubbed.
#:
#: Skipping these cannot lose a real address, because none of these is a top-level domain.
_ASSET_SUFFIX: Final[re.Pattern[str]] = re.compile(
    r"(?i)\.(webp|png|jpe?g|gif|svg|ico|bmp|avif|css|js|mjs|json|woff2?|ttf|eot|mp4|webm|pdf)$"
)

#: Any run of digits long enough to be a phone number, with the separators people use. Broader
#: than `phonenumbers` accepts on purpose: a PIN code, a price and a year all match, and the
#: labelling step is where they are marked as things the extractor must *not* report.
#:
#: The trailing guard is `(?!\d)` and not `(?![\w.])`. The stricter version rejected a number at
#: the end of a sentence -- `... or on 097111 74449.` -- and left a real phone number in a fixture
#: bound for a public repository. A boundary that fails closed is not a boundary.
#:
#: The separator class carries the Unicode dashes and `\ufffd` as well as ASCII. A clinic's
#: footer came back mis-decoded, so the gap inside `0124 519 9464` arrived as a replacement
#: character; `\s` does not match it, the number split into two runs too short to match, and the
#: scrubber replaced only half of it. Being generous here costs nothing -- a false match is
#: labelled absent -- while being strict leaves real digits in a published file.
#:
#: The span is `{6,24}` and was `{6,16}`, which is one short of a ten-digit number written with a
#: separator between every digit: `9-2-1-7-8-3-6-5-2-1` needs 17, matched nothing, and six real
#: numbers went into the corpus untouched. The digit count is checked afterwards, in
#: `replace_phone`, so widening the span here costs nothing and closes that class.
#: The separators are spelled as escapes so this file stays ASCII and a reader can tell a hyphen
#: from an en dash, which is the whole reason ruff objects to the literal characters.
#:
#: Beyond ASCII: the Unicode dashes, the middle dot and the bullet, which sites use as visual
#: separators -- one clinic's footer prints `0124 · 519 9464` -- and `�`, for a page
#: that arrives mis-decoded. `\s` matches none of them, so the number split into two runs too
#: short to match and the scrubber replaced only half of it, leaving real digits in a published
#: file. Being generous here costs nothing, because a false match is labelled absent; being
#: strict costs somebody their phone number.
_SEPARATORS: Final[str] = "\\d\\s\\-().\\u00b7\\u2010-\\u2015\\u2022\\ufffd"
_PHONE: Final[re.Pattern[str]] = re.compile(rf"(?<![\w.])(\+?\d[{_SEPARATORS}]{{6,24}}\d)(?!\d)")

#: Numbers that cannot be a contact detail by their very shape, and which the scrubber therefore
#: leaves exactly as served. None of them is anybody's personal data, and substituting into them
#: does real damage:
#:
#: * an ISO date became `3644-44-80`, so six fixtures held a `<time>` element no page ever served;
#: * an IPv4 address became `103.638.519.26`, mangling a link to a clinic's own booking page;
#: * a longitude became `02.911353`, and a leading zero is **invalid JSON** -- which silently
#:   disabled structured extraction for all three of that site's pages and understated recall by
#:   a corpus artefact rather than a defect.
#:
#: Nothing is hidden by skipping them. No phone number is written as a single dot with three
#: decimals, and if one ever were, the leak gate asks `phonenumbers` rather than this pattern --
#: so it would fail loudly instead of publishing quietly.
_NOT_A_CONTACT_SHAPE: Final[tuple[re.Pattern[str], ...]] = (
    # An ISO date, and the `dd.mm.yyyy` a news list prints: `<span class="mj-news-date">`.
    re.compile(r"^(19|20)\d\d-\d{1,2}-\d{1,2}$"),
    re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2,4}$"),
    # An IPv4 address.
    re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$"),
    # A decimal: a coordinate, a price, a version.
    re.compile(r"^\d{1,4}\.\d{2,}$"),
    # A number with dot thousand separators. One clinic's site carries an Indonesian template,
    # prices and all: `Earn 1.418.793 points`.
    re.compile(r"^\d{1,3}(?:\.\d{3})+$"),
    # A year range, which is what a copyright line and an award badge print: `Copyright © 2019-2024`
    # became `4159-6201`, and `Satya Developers (2012 - 2023)` became `(0112 - 5880)`.
    re.compile(r"^(19|20)\d\d\s*[-\u2013]\s*((19|20)?\d\d)$"),
)


def is_structural(value: str) -> bool:
    """Whether this is a date, an IP address or a decimal number rather than a contact."""
    bare = value.strip()
    return any(pattern.match(bare) for pattern in _NOT_A_CONTACT_SHAPE)


#: A `tel:` or `mailto:` with percent-escapes in it, which is how five numbers survived
#: scrubbing: `tel:099103%2008147` splits into `099103` and `2008147` around the `%`, both too
#: short for `_PHONE`, so the real number was published verbatim -- and the leak gate, running the
#: same pattern, could not see it either. Worse, `tel:%2B919876543210` matches nothing at all.
#:
#: Decoded in these two schemes only, where a percent-escape has exactly one meaning.
#: `app/crawl/contacts.py` unquotes hrefs as well, so a decoded fixture is the string the
#: extractor works from in any case.
_LINK_HREF: Final[re.Pattern[str]] = re.compile(r"""(?i)(?:tel|mailto):[^"'<>\s]*""")


#: Elements no contact extractor reads, and which are most of a page's bytes.
_STRIP: Final[tuple[str, ...]] = ("style", "svg", "noscript", "iframe", "canvas", "template")

#: Every `<script>` block, with its opening tag captured so one can be told from another.
_SCRIPT: Final[re.Pattern[str]] = re.compile(r"(?is)(<script\b[^>]*>)(.*?)(</script\s*>)")

#: The one kind of script the extractors do read (`app/crawl/structured.py`, via extruct).
_IS_JSONLD: Final[re.Pattern[str]] = re.compile(r"(?i)application/ld\+json")

_COMMENT: Final[re.Pattern[str]] = re.compile(r"(?s)<!--.*?-->")

#: An SVG that was HTML-escaped into an attribute value, which `_STRIP` cannot see: it is text
#: in a `data-...="&lt;svg&gt;&lt;path d=&quot;M464 32H48..."`, not an element. One such page put
#: 31 SVG coordinates into its labels -- `4.6-4.9-8.8-53.6 5` and the like -- which is noise a
#: reviewer has to read past, and a reviewer who reads past things stops reviewing.
#: Written for double-quoted attributes only. A single-quoted one holding escaped SVG is
#: possible, was not seen in the corpus, and a pattern with a back-reference for the quote is
#: the kind that looks right and silently matches nothing -- this one did, for one revision.
_ESCAPED_SVG: Final[re.Pattern[str]] = re.compile(r'(?is)="[^"]*&lt;svg[^"]*"')

#: An inline `style="..."`, which `_STRIP` cannot reach: it is an attribute, not an element. No
#: extractor reads CSS, and a `box-shadow: 0 0 7px #00000029` put a "phone number" in the labels.
#: The attribute is kept and emptied rather than removed, so the markup keeps its shape.
_INLINE_STYLE: Final[re.Pattern[str]] = re.compile(r'(?is)\sstyle="[^"]*"')

#: A base64 image inlined into the markup. Megabytes of it, and nothing reads it.
_DATA_URI: Final[re.Pattern[str]] = re.compile(r"(?i)data:[a-z/+.-]+;base64,[A-Za-z0-9+/=]+")

#: A paragraph of somebody's prose. Replaced rather than deleted: the extractors walk text nodes,
#: and a page with no text at all would exercise a path no real page takes.
_PROSE_WORDS: Final[int] = 12
_PROSE_MARKER: Final[str] = "[prose removed for the corpus]"

#: India's mobile ranges start 6-9 and are ten digits; anything else of that length with an area
#: code is a landline. Kept apart because `phonenumbers` reports a line type and this codebase
#: stores it as `phone_type`, so a corpus that turned every landline into a mobile could not
#: measure whether that field is right.
_MOBILE_FIRST: Final[frozenset[str]] = frozenset({"6", "7", "8", "9"})

_ROLE_LOCAL_PARTS: Final[frozenset[str]] = frozenset(
    {
        "info", "contact", "enquiry", "enquiries", "inquiry", "inquiries", "hello", "hi",
        "sales", "support", "help", "admin", "office", "mail", "email", "care",
        "reception", "frontdesk", "appointments", "booking", "bookings", "service",
        "team", "hr", "careers", "jobs", "accounts", "billing", "finance", "noreply",
        "no-reply", "donotreply",
    }
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Substitution:
    """One value replaced, and what replaced it."""

    kind: str  # "email" | "phone"
    original_shape: str
    replacement: str
    #: Where it sat, as the fixture will show it: `mailto`, `tel`, `whatsapp`, `jsonld`, `text`.
    context: str


@dataclass(slots=True)
class Scrubbed:
    """A fixture and the worklist for labelling it."""

    html: str
    substitutions: list[Substitution] = field(default_factory=list)

    @property
    def emails(self) -> list[str]:
        return [s.replacement for s in self.substitutions if s.kind == "email"]

    @property
    def phones(self) -> list[str]:
        return [s.replacement for s in self.substitutions if s.kind == "phone"]


def _drop_unless_jsonld(match: re.Match[str]) -> str:
    """Keeps a JSON-LD block where it was and removes every other script.

    Decided per block rather than by stripping everything and putting the keepers back. The
    first version did the latter and had to re-insert them before `</head>`, which moved them,
    changed the document order extruct reads, and would have dropped them outright on a page
    with no `<head>`. A mistake here removes evidence the labels claim is present, so the simple
    version is the correct one.
    """
    return match.group(0) if _IS_JSONLD.search(match.group(1)) else ""


def reduce_page(html: str) -> str:
    """Everything the extractors read, and nothing else."""
    kept = _SCRIPT.sub(_drop_unless_jsonld, html)
    for tag in _STRIP:
        kept = re.sub(rf"(?is)<{tag}\b.*?</{tag}\s*>", "", kept)
        kept = re.sub(rf"(?is)<{tag}\b[^>]*/?>", "", kept)
    kept = _COMMENT.sub("", kept)
    kept = _ESCAPED_SVG.sub('="svg removed"', kept)
    kept = _INLINE_STYLE.sub(' style=""', kept)
    kept = _LINK_HREF.sub(lambda m: unquote(m.group(0)), kept)
    kept = _DATA_URI.sub("data:removed", kept)
    # Collapse the whitespace a stripped page leaves behind, so a fixture reads in a diff.
    kept = re.sub(r"\n{3,}", "\n\n", kept)
    return re.sub(r"[ \t]{4,}", "  ", kept)


def _token(seed: str, salt: str) -> int:
    """A stable number from a string, so re-harvesting a page produces the same fixture.

    Hashed rather than counted, because a counter would renumber every substitution on a page
    whenever one earlier in it changed, and the whole corpus would churn in the diff.
    """
    digest = hashlib.sha256(f"{salt}:{seed}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def _slug_word(slug: str) -> str:
    """A readable stem for synthetic hosts, so a fixture is recognisable in a failure message."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
    return cleaned[:20] or "site"


def _synthetic_email(local: str, domain: str, slug: str) -> str:
    """An address of the same shape, on the same top-level domain.

    The local part keeps its separators, because `app/normalize/emails.py` treats `info@` and
    `priya.sharma@` differently and the corpus has to keep that distinction readable. The
    domain's labels are replaced and its top-level domain is not: a `.test` domain is refused by
    `has_real_tld` and would send every fixture down a path no real page takes (ADR-0020).
    """
    parts = domain.split(".")
    tld = parts[-1] if len(parts) > 1 else "example"
    second = parts[-2] if len(parts) > 2 else ""
    number = _token(f"{local}@{domain}", slug) % 90 + 10

    shape = re.sub(r"[A-Za-z]+", "x", local)
    shape = re.sub(r"\d+", "9", shape)
    local_out = {
        "x.x": f"first{number}.last{number}",
        "x_x": f"first{number}_last{number}",
        "x-x": f"first{number}-last{number}",
    }.get(shape, f"box{number}")
    # A role account keeps its own local part: which mailbox it is, is the fact `email_role`
    # records, and replacing `info` with `box17` would delete the thing under test.
    if local.lower() in _ROLE_LOCAL_PARTS:
        local_out = local.lower()

    host = f"{_slug_word(slug)}-{number}" + (f".{second}" if second else "")
    return f"{local_out}@{host}.{tld}".lower()


def _country_digits(digits: list[str]) -> int:
    """Where the search *starts*: country code, trunk prefix, and area code or series digit.

    `_synthetic_phone` widens this when it has to, so being wrong here costs a few more real
    digits in the fixture, not a broken number. Being right here is what keeps it to the few.

    The country code and the trunk prefix are kept because they are the shape: `+91 ...` must
    stay `+91 ...`, and a national `0...` must keep its leading zero, which is the digit
    `app/normalize/phones.py` exists to remove.

    The area code is kept for the same reason one level down. `+91 124 456 7890` is a Gurugram
    landline; replacing the `124` with a mobile prefix makes every landline in the corpus a
    mobile and quietly deletes the only test of `phone_type`.

    A mobile keeps one digit for exactly the same reason, in the other direction. In India the
    first subscriber digit *is* the series -- 6 to 9 is a mobile, anything else is not -- so a
    body walked from an unconstrained hash turns `+91 97111 74449` into `+91 35643 89586`, a
    landline. Keeping that one digit is the mirror of keeping the area code, and it is the same
    `phone_type` test being protected.
    """
    prefix = 0
    if digits[:2] == ["9", "1"] and len(digits) > 10:
        prefix = 2
    elif digits[:1] == ["0"]:
        prefix = 1

    rest = digits[prefix:]
    if len(rest) < 10:
        return prefix
    if rest[0] not in _MOBILE_FIRST:
        # A landline: keep enough of the area code that the number stays one. Indian STD codes
        # are two to four digits after the trunk prefix; three covers the metros and Gurugram.
        return prefix + 3
    return prefix + 1


#: How far to walk looking for a number phonenumbers accepts. Allocated ranges are contiguous
#: blocks, so a valid one is usually within a few steps; this bound exists so a range with none
#: at all fails fast rather than hanging a harvest.
_SEARCH: Final[int] = 400

#: The fewest digits that must still be synthetic. Keeping more of the original is how an odd
#: range is matched, and this is the line it may not cross: five free digits mean a replacement
#: cannot coincide with the number it replaced except at one chance in a hundred thousand.
_FREE_DIGITS: Final[int] = 5

#: Treated as one class, because phonenumbers cannot always tell them apart: several Indian
#: ranges answer `FIXED_LINE_OR_MOBILE`. The distinction the corpus needs is mobile against
#: landline -- `+91 124` must not come out a mobile -- not a label phonenumbers itself hedges.
_MOBILE_KINDS: Final[frozenset[str]] = frozenset({"mobile", "fixed_line_or_mobile"})


def _fill(original: str, digits: list[str], kept: int, body: str) -> str:
    """`original` with its digits after `kept` replaced by `body`, separators untouched."""
    out: list[str] = []
    taken = 0
    for char in original:
        if not char.isdigit():
            out.append(char)
            continue
        if taken < kept:
            out.append(digits[taken])
        else:
            index = taken - kept
            out.append(body[index] if index < len(body) else "0")
        taken += 1
    return "".join(out)


def _is_mobile(value: str) -> bool | None:
    """Whether this is a mobile, or None when it is not a phone number at all."""
    parsed = parse(value, "IN")
    return line_type(parsed) in _MOBILE_KINDS if parsed else None


def _synthetic_phone(original: str, slug: str) -> str:
    """The same digits-and-separators shape, with digits that belong to nobody.

    **Every candidate is checked against `phonenumbers`, for two properties, not one.**

    *It has to be dialable.* The first version filled the body from a hash and stopped there,
    and every synthetic landline it produced was invalid -- `0124 940 4482` became
    `0124 953 3188`, which is in no allocated range. The corpus then held `tel:` links the
    pipeline refuses to read, so those pages could not be labelled with the number they plainly
    show, and recall was being measured against values nothing could ever extract.

    *And it has to be the same kind of number.* `phone_type` is a stored field, so a landline
    that comes out a mobile deletes the only test of it. Checking the type beats guessing at
    prefix lengths: keeping the area code saved `+91 124 456 7890` and keeping the series digit
    saved `+91 97111 74449`, but `1800 123 4567` needs four digits kept, and `6123456789` is a
    landline whose first digit is in the mobile range. So the search keeps widening -- one more
    real digit each round -- until both properties hold, and stops at `_FREE_DIGITS`.

    One condition covers both, and the third case falls out of it: when the original is not a
    number phonenumbers reads, the replacement has to be unreadable too. A page printing
    `+91 124 9684614`, which `phonenumbers` refuses, must stay a page the pipeline cannot read
    it from -- otherwise the fixture claims a dialable number the business never published, and
    recall on the corpus comes out better than recall in the world.

    Candidates are tried in a deterministic order, so a re-harvest produces the same fixture.

    `parse` and `line_type` are consulted here, and only to ask what kind of number this is.
    That is a different question from the one the report asks, which is whether the pipeline
    found the right one.
    """
    digits = [c for c in original if c.isdigit()]
    seed = _token(original, slug)
    wanted = _is_mobile(original)
    widest = max(len(digits) - _FREE_DIGITS, 0)

    # Walked numerically from a hashed start rather than re-hashed per attempt: allocated ranges
    # are contiguous blocks, so stepping finds one, where twelve independent hashes did not.
    # Then widened, because stepping only moves the last few digits -- 400 steps never reach a
    # different range, which is what `1800` needs.
    for kept in range(min(_country_digits(digits), widest), widest + 1):
        body_length = max(len(digits) - kept, 0)
        for step in range(_SEARCH):
            body = f"{(seed + step) % (10 ** body_length):0{body_length}d}" if body_length else ""
            candidate = _fill(original, digits, kept, body)
            if _is_mobile(candidate) == wanted:
                return candidate

    # Unreachable for anything phonenumbers reads, since the last round keeps every digit but
    # five of a number it already accepted. Returned rather than raised: a harvest that dies on
    # one number in one page is worse than a fixture a reviewer throws out.
    return _fill(original, digits, widest, "0" * _FREE_DIGITS)
def _context_of(html: str, start: int) -> str:
    """Where a value sat, which is what decides how strong the evidence is.

    `mailto:` and `tel:` are somebody pointing at their own contact details; text is a guess;
    JSON-LD is a machine-readable claim. The extractors weigh these differently
    (`CONFIDENCE_LINKED` against `CONFIDENCE_TEXT`), so the corpus has to record which it was or
    the report cannot tell a weak find from a strong one.

    Read from the markup immediately before the match, not from a window of a hundred
    characters: a window picked up the `wa.me` on the line above and labelled a plain text
    address as a WhatsApp link.
    """
    before = html[max(0, start - 400) : start]
    tag_open = before.rfind("<")
    tag_close = before.rfind(">")

    if tag_open > tag_close:
        attribute = before[tag_open:].lower()
        if "mailto:" in attribute:
            return "mailto"
        if "tel:" in attribute or "callto:" in attribute:
            return "tel"
        if "wa.me" in attribute or "whatsapp" in attribute:
            return "whatsapp"

    lowered = before.lower()
    opened = lowered.rfind("application/ld+json")
    if opened != -1 and lowered.rfind("</script") < opened:
        return "jsonld"
    return "text"


def _drop_prose(html: str) -> str:
    """Replaces long runs of text that carry no contact value.

    Replaced, not deleted: the extractors walk text nodes, and a page with no text would
    exercise a path no real page takes. Anything containing an `@` or a run of digits is left
    alone -- this is a privacy and copyright measure, not a way of shrinking the file, and
    removing a line that turned out to hold a phone number would silently delete a label.
    """

    def keep_or_replace(match: re.Match[str]) -> str:
        text = match.group(0)
        if "@" in text or re.search(r"\d{4}", text):
            return text
        if len(text.split()) < _PROSE_WORDS:
            return text
        return _PROSE_MARKER

    # Text between tags only, so no attribute is touched.
    return re.sub(r"(?<=>)[^<>]{40,}(?=<)", keep_or_replace, html)


def scrub(html: str, *, slug: str, reduce: bool = True) -> Scrubbed:
    """A publishable fixture, and the list of what was substituted into it.

    `reduce` strips the stylesheets, scripts and inline SVG first. On by default, because a page
    scrubbed without it produced 260 contact-shaped matches of CSS lengths and SVG coordinates.
    """
    if reduce:
        html = reduce_page(html)

    result = Scrubbed(html="")
    seen: dict[str, str] = {}
    phone_source = html

    def replace_email(match: re.Match[str]) -> str:
        local, joiner, domain = match.group(1), match.group(2), match.group(3)
        flat_domain = re.sub(r"\s*(?:\(\s*dot\s*\)|\[\s*dot\s*\])\s*", ".", domain)
        flat_domain = re.sub(r"\s+", "", flat_domain)
        if _NUMERIC_TLD.search(flat_domain) or _ASSET_SUFFIX.search(flat_domain):
            return match.group(0)
        key = f"email:{local}@{flat_domain}".lower()
        if key not in seen:
            seen[key] = _synthetic_email(local, flat_domain, slug)
            result.substitutions.append(
                Substitution(
                    kind="email",
                    original_shape=re.sub(r"[A-Za-z]", "x", f"{local}@{flat_domain}"),
                    replacement=seen[key],
                    context=_context_of(html, match.start()),
                )
            )
        new_local, _, new_domain = seen[key].partition("@")
        # The joiner is kept exactly: an obfuscated `(at)` is a case the extractor has to
        # decode, and normalising it here would delete the test.
        return f"{new_local}{joiner}{new_domain}"

    def replace_phone(match: re.Match[str]) -> str:
        original = match.group(1)
        digits = [c for c in original if c.isdigit()]
        if not 7 <= len(digits) <= 15:
            return original
        if is_structural(original):
            return original
        key = f"phone:{''.join(digits)}"
        if key not in seen:
            seen[key] = _synthetic_phone(original, slug)
            result.substitutions.append(
                Substitution(
                    kind="phone",
                    original_shape=re.sub(r"\d", "9", original),
                    replacement=seen[key],
                    # Offsets in the string being scanned. Using the original's labelled matches
                    # by whatever sat at that position before the emails changed length.
                    context=_context_of(phone_source, match.start()),
                )
            )
        return seen[key]

    scrubbed = _EMAIL.sub(replace_email, html)
    phone_source = scrubbed
    result.html = _drop_prose(_PHONE.sub(replace_phone, scrubbed))
    return result
