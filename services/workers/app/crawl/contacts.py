"""Pulling contact details out of a fetched page (docs/06 sections 5.2 and 6, task 3.8).

This is where emails come from. A maps listing never carries one; a business's own contact page
usually does, and often in a form designed to defeat exactly this. So the obfuscations are undone
-- `info [at] clinic [dot] com`, entity-encoded `&#64;`, a reversed string in CSS -- but only the
deterministic ones, and every value is marked `derivation=found` only when it was literally present
after decoding. CLAUDE.md is explicit: a contact value that is not in a source may not be invented,
and nothing here guesses.

Two rules shape the whole module.

**A `mailto:` or `tel:` href outranks text.** The author put the address in a link because it is
the address; a string in a paragraph may be an example, a customer's address in a testimonial, or
a Cloudflare placeholder. Both are read, and the href wins on conflict.

**A phone is only kept if `phonenumbers` says it is possible for a real region.** Indian sites
write numbers eleven different ways and a regex that accepts all of them also accepts GST numbers,
PIN codes, prices and years. The library's own validity test is the filter.
"""

import html
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import unquote, urlsplit

import phonenumbers
from scrapling import Selector

from app.normalize.emails import ROLE_LOCAL_PARTS as EMAIL_ROLE_LOCAL_PARTS
from app.normalize.emails import is_role as is_role_email
from app.normalize.emails import normalize as normalize_email
from app.normalize.phones import line_type
from app.normalize.phones import parse as parse_phone

#: Re-exported: this was the home of the role list and every caller and test knows it from
#: here. The list itself is `app/normalize/emails.py`, because the place index's mapping needs
#: the same answer and will not import a web crawler to get it.
ROLE_LOCAL_PARTS = EMAIL_ROLE_LOCAL_PARTS

#: `@` written so a scraper misses it. Each alternative is a literal substitution, never a guess.
_AT = r"(?:@|\(\s*at\s*\)|\[\s*at\s*\]|\{\s*at\s*\}|\s+at\s+|&#0*64;|&commat;|%40)"
_DOT = r"(?:\.|\(\s*dot\s*\)|\[\s*dot\s*\]|\{\s*dot\s*\}|\s+dot\s+|&#0*46;|%2e)"

#: Deliberately strict on the local part: a looser one swallows the preceding word of a sentence.
_EMAIL_RE = re.compile(
    rf"(?i)\b([a-z0-9!#$%&'*+/=?^_`{{|}}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{{|}}~-]+)*)"
    rf"\s*{_AT}\s*"
    rf"((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\s*{_DOT}\s*)+[a-z]{{2,24}})"
)

#: Hyphens a word processor produced. Folded to ASCII before matching rather than carried in
#: every pattern, where a literal en dash is indistinguishable from a hyphen when reading it.
_FANCY_DASHES = str.maketrans(dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015", "-"))

#: Runs of digits long enough to be a phone somewhere, with the punctuation sites use.
_PHONE_CANDIDATE = re.compile(r"(?:\+?\d[\d\s().-]{6,20}\d)")

#: Things shaped like phone numbers that are not. India's GSTIN and PIN, years, prices, and the
#: long digit strings in tracking ids.
_NOT_A_PHONE_CONTEXT = re.compile(r"(?i)\b(gstin?|pan|cin|pin ?code|invoice|order|reg\.? ?no)\b")


class SocialPlatform(StrEnum):
    """Platforms we store a handle for. docs/08 row 50: the URL only, never crawled."""

    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    X = "x"
    YOUTUBE = "youtube"
    WHATSAPP = "whatsapp"


_SOCIAL_HOSTS: tuple[tuple[SocialPlatform, re.Pattern[str]], ...] = (
    (SocialPlatform.FACEBOOK, re.compile(r"(?i)(^|\.)(facebook\.com|fb\.com|fb\.me)$")),
    (SocialPlatform.INSTAGRAM, re.compile(r"(?i)(^|\.)(instagram\.com|instagr\.am)$")),
    (SocialPlatform.LINKEDIN, re.compile(r"(?i)(^|\.)linkedin\.com$")),
    (SocialPlatform.X, re.compile(r"(?i)(^|\.)(twitter\.com|x\.com|t\.co)$")),
    (SocialPlatform.YOUTUBE, re.compile(r"(?i)(^|\.)(youtube\.com|youtu\.be)$")),
    (SocialPlatform.WHATSAPP, re.compile(r"(?i)(^|\.)(wa\.me|whatsapp\.com|api\.whatsapp\.com)$")),
)

#: Paths on a social host that are the platform's own, not a business's page.
_SOCIAL_NOISE = re.compile(
    r"(?i)^/(sharer|share|intent|dialog|plugins|tr|login|signup|help|about|legal|policies"
    r"|privacy|terms|home|watch|embed|oembed)(/|$|\?)"
)


class _HasFromLink(Protocol):
    """Anything that knows whether it came from a link. Keeps `preferred` generic."""

    @property
    def from_link(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class Email:
    value: str
    #: True when it came from a `mailto:` href rather than from text.
    from_link: bool
    #: True when the local part is a role account rather than a named person (docs/06 section 6).
    role_account: bool
    #: True when the address was written obfuscated and decoded here. Still `found`, not inferred:
    #: every character came from the page, only the spelling of `@` and `.` was restored.
    deobfuscated: bool = False


@dataclass(frozen=True, slots=True)
class Phone:
    #: E.164, per CLAUDE.md.
    value: str
    from_link: bool
    #: What `phonenumbers` calls it: mobile, fixed_line, toll_free, and so on.
    line_type: str


@dataclass(frozen=True, slots=True)
class Social:
    platform: SocialPlatform
    url: str
    #: The canonical handle, lowercased, without `@` or query (docs/06 section 6).
    handle: str


def decode_entities(text: str) -> str:
    """Entities, percent-escapes and fancy dashes, so the patterns below only need ASCII.

    `&#64;` and `%40` have to read as `@` before an address can be matched, and a phone written
    with an en dash has to read as one written with a hyphen.
    """
    return unquote(html.unescape(text)).translate(_FANCY_DASHES)


def _tidy_email(local: str, domain: str) -> str | None:
    """One candidate from a page, un-obfuscated, then held to the shared rule.

    Only the un-obfuscating is here. `name (at) clinic (dot) in` is about how a page hides an
    address from scrapers, which nothing but the crawler can know about; what counts as a usable
    business address is `app/normalize/emails.py`, because every source needs the same answer.
    """
    local = re.sub(r"\s+", "", local)
    domain = re.sub(r"(?i)\s*(\(|\[|\{)?\s*dot\s*(\)|\]|\})?\s*", ".", domain)
    domain = re.sub(r"\s+", "", domain)
    if not local or not domain:
        return None
    return normalize_email(f"{local}@{domain}")


def emails_from(html_text: str | bytes, *, selector: Selector | None = None) -> list[Email]:
    """Every address on the page, `mailto:` links first and text after.

    Order matters to the caller: the first entry is the one to prefer, and a link beats a string.
    """
    page = selector or _parse(html_text)
    found: dict[str, Email] = {}

    for anchor in page.css("a"):
        href = decode_entities((anchor.attrib.get("href") or "").strip())
        if not href.lower().startswith("mailto:"):
            continue
        # One mailto can carry several addresses and a query: `mailto:a@x,b@x?subject=Hi`.
        for part in href[7:].split("?", 1)[0].split(","):
            match = _EMAIL_RE.search(part)
            if match is None:
                continue
            address = _tidy_email(match.group(1), match.group(2))
            if address and address not in found:
                found[address] = Email(address, True, _is_role(address))

    text = decode_entities(page.get_all_text() or "")
    for match in _EMAIL_RE.finditer(text):
        address = _tidy_email(match.group(1), match.group(2))
        if address is None or address in found:
            continue
        found[address] = Email(
            address, False, _is_role(address), deobfuscated="@" not in match.group(0)
        )
    return list(found.values())


def _is_role(address: str) -> bool:
    return is_role_email(address)


def phones_from(
    html_text: str | bytes, *, region: str | None = None, selector: Selector | None = None
) -> list[Phone]:
    """Every number on the page that `phonenumbers` accepts, in E.164.

    `region` is the company's country (ISO-3166 alpha-2) and decides how a local number without a
    country code is read. Without it only numbers written with `+` survive, which is the honest
    outcome: `98765 43210` is a valid mobile in India and nothing at all without knowing that.
    """
    page = selector or _parse(html_text)
    found: dict[str, Phone] = {}

    for anchor in page.css("a"):
        href = decode_entities((anchor.attrib.get("href") or "").strip())
        lowered = href.lower()
        if lowered.startswith(("tel:", "callto:")):
            raw = href.split(":", 1)[1]
        elif _whatsapp_number(href):
            raw = f"+{_whatsapp_number(href)}"
        else:
            continue
        _record_phone(found, raw, region, from_link=True)

    for line in _text_lines(page):
        if _NOT_A_PHONE_CONTEXT.search(line):
            continue
        for match in _PHONE_CANDIDATE.finditer(line):
            _record_phone(found, match.group(0), region, from_link=False)
    return list(found.values())


def _record_phone(
    found: dict[str, Phone], raw: str, region: str | None, *, from_link: bool
) -> None:
    parsed = parse_phone(raw, region)
    if parsed is None:
        return
    value = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    existing = found.get(value)
    if existing is None or (from_link and not existing.from_link):
        found[value] = Phone(value, from_link, line_type(parsed))


def _whatsapp_number(href: str) -> str | None:
    """The digits in a `wa.me/919876543210` or `?phone=` link, which is a phone and a social."""
    parts = urlsplit(href)
    if not any(
        pattern.search(parts.netloc.lower())
        for platform, pattern in _SOCIAL_HOSTS
        if platform is SocialPlatform.WHATSAPP
    ):
        return None
    digits = re.sub(r"\D", "", parts.path)
    if not digits:
        query = re.search(r"(?i)phone=(\+?\d+)", parts.query or "")
        digits = re.sub(r"\D", "", query.group(1)) if query else ""
    return digits or None


def social_from_url(href: str) -> Social | None:
    """The profile a single URL points at, or None if it is not one we store.

    Separate from `socials_from` because social links do not only arrive as anchors on a page:
    the bulk place datasets publish them as a list of URLs (ADR-0017), and a second copy of the
    host patterns and handle rules is a second place for them to drift out of agreement.
    """
    href = (href or "").strip()
    if not href.lower().startswith(("http://", "https://")):
        return None
    parts = urlsplit(href)
    host = parts.netloc.lower().split(":")[0]
    for platform, pattern in _SOCIAL_HOSTS:
        if not pattern.search(host):
            continue
        handle = _handle(platform, parts.path, parts.query)
        return Social(platform, href, handle) if handle is not None else None
    return None


def socials_from(html_text: str | bytes, *, selector: Selector | None = None) -> list[Social]:
    """Social profile URLs found on the page. docs/08 row 50: stored, never crawled."""
    page = selector or _parse(html_text)
    found: dict[tuple[SocialPlatform, str], Social] = {}
    for anchor in page.css("a"):
        social = social_from_url(anchor.attrib.get("href") or "")
        if social is not None:
            found.setdefault((social.platform, social.handle), social)
    return list(found.values())


def _handle(platform: SocialPlatform, path: str, query: str) -> str | None:
    if _SOCIAL_NOISE.match(path or "/"):
        return None
    if platform is SocialPlatform.WHATSAPP:
        digits = re.sub(r"\D", "", path) or (
            re.sub(r"\D", "", m.group(1)) if (m := re.search(r"(?i)phone=(\+?\d+)", query)) else ""
        )
        return digits or None
    segments = [s for s in path.split("/") if s]
    if not segments:
        return None
    # LinkedIn's handle is the second segment: /company/acme, /in/priya-sharma.
    if platform is SocialPlatform.LINKEDIN:
        if segments[0].lower() in ("company", "in", "school", "showcase") and len(segments) > 1:
            return f"{segments[0].lower()}/{segments[1].lower()}"
        return None
    return segments[0].lstrip("@").lower() or None


def _parse(html_text: str | bytes) -> Selector:
    return Selector(
        html_text if isinstance(html_text, str) else html_text.decode("utf-8", errors="ignore")
    )


def _text_lines(page: Selector) -> Iterator[str]:
    """Page text split into lines, so a "GSTIN" label cannot disqualify a whole page's numbers."""
    for line in decode_entities(page.get_all_text() or "").splitlines():
        stripped = line.strip()
        if stripped:
            yield stripped


def contact_bundle(
    html_text: str | bytes, *, region: str | None = None
) -> tuple[list[Email], list[Phone], list[Social]]:
    """All three in one parse, which is the way the crawler calls it."""
    page = _parse(html_text)
    return (
        emails_from("", selector=page),
        phones_from("", region=region, selector=page),
        socials_from("", selector=page),
    )


def preferred[T: _HasFromLink](values: Iterable[T]) -> T | None:
    """The one to show in a single-value column: a linked value before a scraped one."""
    ordered = sorted(values, key=lambda v: not v.from_link)
    return ordered[0] if ordered else None
