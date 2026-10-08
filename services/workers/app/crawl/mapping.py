"""A crawled page turned into field values with provenance (task 3.8, CLAUDE.md provenance rule).

Every value says where it came from, when, by what method and how sure we are. For a crawl the
method is always `crawl` and the derivation is always `found`, including for a decoded obfuscated
address: `info [at] clinic [dot] com` yields `info@clinic.com` where every character was on the
page and only the spelling of `@` and `.` was restored. Nothing here infers, so nothing here is
`ai` or `derived_pattern`.

**Confidence says how the page said it, not how much we like the answer.** A business that filled
in a schema.org block published the value deliberately; a `mailto:` link is the author pointing at
their own address; a string matched in running text might be a customer's address in a testimonial.
Those are three different levels of evidence and they get three different numbers.

**One value per field, by design and not by omission.** `field_values.is_current` is scoped to
(entity, field), so three emails written as three `email` rows would retire two of them on the way
in. docs/08's capability map is singular for the same reason. A clinic with `info@` and
`appointments@` therefore keeps the better one here, and the multi-value contact model belongs to
Phase 4, which is the phase named for it. Recorded rather than quietly solved with an invented
field key.

**A site's own rating is not stored.** A page saying it has 4.9 stars is marketing, and putting it
in the same column as Google's rating would make the two indistinguishable in the grid.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.connectors.types import FieldValue
from app.crawl.contacts import (
    SocialPlatform,
    contact_bundle,
    phones_from,
    preferred,
    socials_from,
)
from app.crawl.structured import Business, business_from

SOURCE_KEY = "website"

#: The business published it in a machine-readable block. The strongest evidence a crawl can get:
#: nobody fills in `LocalBusiness` by accident.
CONFIDENCE_STRUCTURED = 0.8

#: The author linked it. `mailto:` and `tel:` are someone pointing at their own contact details.
CONFIDENCE_LINKED = 0.75

#: Matched in running text. Usually right and sometimes a testimonial's phone number, so it is
#: believed less than either of the above and is still recorded as found, because it was.
CONFIDENCE_TEXT = 0.55

#: One field per platform, as docs/08's capability map has it.
_SOCIAL_FIELDS: dict[SocialPlatform, str] = {
    SocialPlatform.FACEBOOK: "facebook",
    SocialPlatform.INSTAGRAM: "instagram",
    SocialPlatform.LINKEDIN: "linkedin",
    SocialPlatform.X: "x",
    SocialPlatform.YOUTUBE: "youtube",
    SocialPlatform.WHATSAPP: "whatsapp",
}


@dataclass(frozen=True, slots=True)
class PageFacts:
    """What one page yielded, before it is merged with the other pages of the same site."""

    url: str
    values: tuple[FieldValue, ...]
    #: Links worth following, discovered while reading this page.
    emails_found: int = 0
    phones_found: int = 0


def values_from_page(
    html: str | bytes,
    *,
    page_url: str,
    observed_at: datetime | None = None,
    region: str | None = None,
) -> PageFacts:
    """Everything one page says about the business whose site it is.

    `region` is the company's country as ISO-3166 alpha-2, and it decides whether `98765 43210`
    reads as a phone number at all. Without it only numbers written with a country code survive.
    """
    when = observed_at or datetime.now(UTC)
    text = html if isinstance(html, str) else html.decode("utf-8", errors="ignore")

    emails, phones, socials = contact_bundle(text, region=region)
    business = business_from(text, base_url=page_url)

    values: list[FieldValue] = []
    add = _adder(values, page_url=page_url, when=when)

    # Structured data first: anything it covers is better evidence than a regex on the same page,
    # and `add` keeps the first value offered for a field.
    if business is not None:
        _add_structured(add, business, region=region)

    best_email = preferred(emails)
    if best_email is not None:
        add(
            "email",
            best_email.value,
            CONFIDENCE_LINKED if best_email.from_link else CONFIDENCE_TEXT,
        )
    best_phone = preferred(phones)
    if best_phone is not None:
        add(
            "phone",
            best_phone.value,
            CONFIDENCE_LINKED if best_phone.from_link else CONFIDENCE_TEXT,
        )
        add("phone_type", best_phone.line_type, CONFIDENCE_LINKED)

    for social in socials:
        add(_SOCIAL_FIELDS[social.platform], social.handle, CONFIDENCE_LINKED)

    return PageFacts(
        url=page_url,
        values=tuple(values),
        emails_found=len(emails),
        phones_found=len(phones),
    )


def _adder(values: list[FieldValue], *, page_url: str, when: datetime) -> Any:
    """Appends one value per field, keeping the first offered.

    First, not last: callers are ordered strongest evidence first, so a schema.org telephone is
    not overwritten by a number matched in a footer a few lines later.
    """
    taken: set[str] = set()

    def add(field: str, value: Any, confidence: float) -> None:
        if field in taken or value in (None, "", [], {}, ()):
            return
        taken.add(field)
        values.append(
            FieldValue(
                entity_type="company",
                field=field,
                value=value,
                source_key=SOURCE_KEY,
                source_url=page_url,
                observed_at=when,
                method="crawl",
                # Literally present on the page. A decoded `[at]` is still found: every character
                # came from the page and only the spelling was restored (CLAUDE.md).
                derivation="found",
                confidence=confidence,
            )
        )

    return add


def _add_structured(add: Any, business: Business, *, region: str | None) -> None:
    """Fields a schema.org or OpenGraph block published outright."""
    confidence = CONFIDENCE_STRUCTURED
    add("name", business.name, confidence)
    add("description", business.description, confidence)
    if business.emails:
        add("email", business.emails[0], confidence)
    if business.telephones:
        add("phone", _e164(business.telephones[0], region), confidence)
    address = business.address
    if not address.is_empty:
        add("address", _one_line(address), confidence)
        add("city", address.locality, confidence)
        add("state", address.region, confidence)
        add("postal_code", address.postal_code, confidence)
        add("country", address.country, confidence)
    if business.opening_hours:
        add("opening_hours", list(business.opening_hours), confidence)
    add("logo", business.logo, confidence)
    # `sameAs` is the business naming its own profiles, which is better evidence than a footer
    # icon: a footer can link the web agency's Instagram.
    for url in business.same_as:
        platform = _platform_of(url)
        if platform is not None:
            add(_SOCIAL_FIELDS[platform], _handle_of(url, platform), confidence)
    # Deliberately not business.rating: a page's claim about its own stars is marketing, and in
    # the grid it would be indistinguishable from Google's.


def _one_line(address: Any) -> str | None:
    parts = [address.street, address.locality, address.region, address.postal_code]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def _e164(raw: str, region: str | None) -> str | None:
    """A structured-data telephone, normalised the same way a `tel:` link would be."""
    found = phones_from(f'<a href="tel:{raw}">x</a>', region=region)
    return found[0].value if found else None


def _platform_of(url: str) -> SocialPlatform | None:
    found = socials_from(f'<a href="{url}">x</a>')
    return found[0].platform if found else None


def _handle_of(url: str, platform: SocialPlatform) -> str | None:
    found = socials_from(f'<a href="{url}">x</a>')
    return found[0].handle if found and found[0].platform is platform else None


def merge_pages(pages: Iterable[PageFacts]) -> list[FieldValue]:
    """One value per field across a whole site, best evidence winning.

    A homepage, a contact page and an about page disagree often enough that this matters: the
    contact page's `mailto:` should beat the homepage's footer text, and a schema.org block
    anywhere should beat both. Ties go to the page read first, which is the higher-priority one.
    """
    best: dict[str, FieldValue] = {}
    for page in pages:
        for value in page.values:
            current = best.get(value.field)
            if current is None or value.confidence > current.confidence:
                best[value.field] = value
    return list(best.values())


def field_names(values: Sequence[FieldValue]) -> list[str]:
    """The fields a set of values covers, for logs and progress counters."""
    return sorted({v.field for v in values})
