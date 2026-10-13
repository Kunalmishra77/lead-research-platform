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

#: Any run of digits long enough to be a phone number, with the separators people use. Broader
#: than `phonenumbers` accepts on purpose: a PIN code, a price and a year all match, and the
#: labelling step is where they are marked as things the extractor must *not* report.
#:
#: The trailing guard is `(?!\d)` and not `(?![\w.])`. The stricter version rejected a number at
#: the end of a sentence -- `... or on 097111 74449.` -- and left a real phone number in a fixture
#: bound for a public repository. A boundary that fails closed is not a boundary.
_PHONE: Final[re.Pattern[str]] = re.compile(r"(?<![\w.])(\+?\d[\d\s\-().]{6,16}\d)(?!\d)")

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
    """How many leading digits are kept verbatim: country code, trunk prefix, and area code.

    The country code and the trunk prefix are kept because they are the shape: `+91 ...` must
    stay `+91 ...`, and a national `0...` must keep its leading zero, which is the digit
    `app/normalize/phones.py` exists to remove.

    The area code is kept for the same reason one level down. `+91 124 456 7890` is a Gurugram
    landline; replacing the `124` with a mobile prefix makes every landline in the corpus a
    mobile and quietly deletes the only test of `phone_type`.
    """
    prefix = 0
    if digits[:2] == ["9", "1"] and len(digits) > 10:
        prefix = 2
    elif digits[:1] == ["0"]:
        prefix = 1

    rest = digits[prefix:]
    if len(rest) >= 10 and rest[0] not in _MOBILE_FIRST:
        # A landline: keep enough of the area code that the number stays one. Indian STD codes
        # are two to four digits after the trunk prefix; three covers the metros and Gurugram.
        return prefix + 3
    return prefix


def _synthetic_phone(original: str, slug: str) -> str:
    """The same digits-and-separators shape, with digits that belong to nobody.

    India's `9xxxxxxxxx` mobile range is used for the body, because `phonenumbers` validates
    against the real allocation and a corpus of unallocated numbers would be refused by the very
    code it is meant to measure.
    """
    number = _token(original, slug)
    digits = [c for c in original if c.isdigit()]
    kept = _country_digits(digits)
    body_length = max(len(digits) - kept, 0)
    body = f"9{(number % 900000000) + 100000000:09d}"[:body_length]

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
