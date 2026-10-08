"""Structured data a site publishes about itself (docs/06 section 5.1, task 3.8).

A business that has filled in a schema.org `LocalBusiness` block has already told us its name,
address, phone, hours and social profiles, in a form it meant to be machine-read. That makes this
the cheapest and most reliable extraction we have, and the first one to try: anything it yields
needs no regex, no model and no guessing.

Three syntaxes are read -- JSON-LD, microdata and OpenGraph -- via extruct, which docs/02 names.
It is a heavy dependency for what we use: it pulls `rdflib` and `pyrdfa3` for RDFa and `mf2py` for
microformats, neither of which we ask for. The reason to keep it anyway is microdata: older
small-business sites, which are most of this market, mark up a `LocalBusiness` with `itemprop`
attributes, and a correct microdata reader is not a weekend's work. The syntaxes we do not use are
switched off at the call, so the cost is image size rather than time.

**Picking the right node is most of the job.** A page carries several blocks -- `WebSite`,
`BreadcrumbList`, `Organization`, `LocalBusiness`, a `Product` per item -- and Yoast nests them all
under `@graph`. Reading the first one found gives the breadcrumb trail's name as the company name.
So the types are ranked, most specific first.
"""

import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import extruct
import structlog

log = structlog.get_logger(__name__)

#: Only what we read. RDFa and microformats are off: extruct installs their parsers, and switching
#: them off here means we do not also pay for running them on every page.
SYNTAXES = ("json-ld", "microdata", "opengraph")

#: Most specific first. A `Dentist` block is about this business; an `Organization` block on the
#: same page may be the website's publisher, and a `WebSite` block is about the site itself.
BUSINESS_TYPES: tuple[str, ...] = (
    "Dentist",
    "MedicalClinic",
    "MedicalBusiness",
    "Hospital",
    "Physician",
    "Restaurant",
    "FoodEstablishment",
    "Store",
    "LegalService",
    "AccountingService",
    "HomeAndConstructionBusiness",
    "ProfessionalService",
    "HealthAndBeautyBusiness",
    "AutomotiveBusiness",
    "EducationalOrganization",
    "SportsActivityLocation",
    "LodgingBusiness",
    "LocalBusiness",
    "Organization",
    "Corporation",
)

#: Nodes that describe the page or the site rather than the business behind it.
_NOT_A_BUSINESS = frozenset(
    {"WebSite", "WebPage", "BreadcrumbList", "SearchAction", "ItemList", "Product", "Offer"}
)


@dataclass(frozen=True, slots=True)
class Address:
    """A postal address as the site published it.

    No geocoding and no splitting of a free-text blob into components: both invent precision
    the page did not have, and task 3.12 does the second one with a real parser.
    """

    street: str | None = None
    locality: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any((self.street, self.locality, self.region, self.postal_code, self.country))


@dataclass(frozen=True, slots=True)
class Business:
    """What a site's own structured data says about the business it belongs to."""

    #: The schema.org type the data came from, so a caller can see how specific the claim was.
    schema_type: str
    name: str | None = None
    description: str | None = None
    url: str | None = None
    telephones: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    address: Address = field(default_factory=Address)
    #: `sameAs` entries: the business's own social profiles, stated by the business.
    same_as: tuple[str, ...] = ()
    opening_hours: tuple[str, ...] = ()
    logo: str | None = None
    rating: float | None = None
    review_count: int | None = None


def extract(html_text: str | bytes, *, base_url: str = "") -> list[dict[str, Any]]:
    """Every structured-data node on the page, flattened, in no particular order.

    Returns raw dictionaries rather than a parsed shape: a caller wanting one field we have not
    modelled should not have to wait for this module to grow.
    """
    text = html_text if isinstance(html_text, str) else html_text.decode("utf-8", errors="ignore")
    if not text.strip():
        return []
    try:
        # errors="log" rather than the default "strict": a page often carries one hand-written
        # JSON-LD block that is not JSON alongside three good ones, and a strict read throws away
        # the good ones too.
        found = extruct.extract(
            text, base_url=base_url or None, syntaxes=list(SYNTAXES), errors="log"
        )
    except Exception as exc:
        # Still guarded. Losing this page's structured data is survivable -- the regex extractors
        # run regardless -- and losing the crawl to a malformed page is not.
        log.info("structured data unreadable", base_url=base_url, error=str(exc))
        return []

    nodes: list[dict[str, Any]] = []
    for syntax in ("json-ld", "microdata"):
        for node in found.get(syntax) or []:
            nodes.extend(_flatten(node))

    # extruct reads all of a page's JSON-LD or none of it: one hand-written block that is not JSON
    # makes its whole json-ld pass fail, and `errors="log"` logs the failure and still returns
    # nothing for that syntax (checked against 0.18.0, not assumed). A site with three good blocks
    # and one typo is common enough that losing all four is the wrong trade, so the blocks are read
    # here instead, one at a time, when extruct came back empty and the page plainly has some.
    if not found.get("json-ld"):
        for block in json_ld_blocks(text):
            nodes.extend(_flatten(block))
    opengraph = _opengraph_node(found.get("opengraph") or [])
    if opengraph:
        nodes.append(opengraph)
    return nodes


def _flatten(node: Any) -> Iterator[dict[str, Any]]:
    """One node and everything nested inside it. Yoast puts the whole site under `@graph`."""
    if isinstance(node, list):
        for item in node:
            yield from _flatten(item)
        return
    if not isinstance(node, dict):
        return
    graph = node.get("@graph")
    if graph is not None:
        yield from _flatten(graph)
    # Microdata nests the same way under `properties`.
    properties = node.get("properties")
    if isinstance(properties, dict):
        flat = {"@type": node.get("type") or node.get("@type"), **properties}
        yield flat
        for value in properties.values():
            yield from _flatten(value)
        return
    if node.get("@type") or node.get("type"):
        yield node
    for value in node.values():
        if isinstance(value, (dict, list)):
            yield from _flatten(value)


def _opengraph_node(entries: Sequence[Any]) -> dict[str, Any] | None:
    """OpenGraph as a node, so it can be ranked with the others. It is the weakest source."""
    merged: dict[str, Any] = {}
    for entry in entries:
        properties = entry.get("properties") if isinstance(entry, dict) else None
        for key, value in properties or []:
            merged.setdefault(key, value)
    if not merged:
        return None
    return {
        "@type": "OpenGraph",
        "name": merged.get("og:site_name") or merged.get("og:title"),
        "description": merged.get("og:description"),
        "url": merged.get("og:url"),
        "image": merged.get("og:image"),
    }


def _type_names(node: dict[str, Any]) -> list[str]:
    raw = node.get("@type") or node.get("type") or []
    values = raw if isinstance(raw, list) else [raw]
    # A @type can be a full URL: http://schema.org/LocalBusiness.
    return [str(v).rsplit("/", 1)[-1].rsplit("#", 1)[-1] for v in values if v]


def rank_of(node: dict[str, Any]) -> int | None:
    """How business-like this node is: lower is better, None when it is not about a business."""
    names = _type_names(node)
    if not names or any(name in _NOT_A_BUSINESS for name in names):
        return None
    ranks = [BUSINESS_TYPES.index(n) for n in names if n in BUSINESS_TYPES]
    if ranks:
        return min(ranks)
    # OpenGraph is a fallback, never a winner: `og:site_name` is often the site's tagline.
    return len(BUSINESS_TYPES) if "OpenGraph" in names else None


def business_from(html_text: str | bytes, *, base_url: str = "") -> Business | None:
    """The best business node on the page, or None when the site published none.

    Best, not first. A page with a `BreadcrumbList` before its `Dentist` block would otherwise
    name the company after the breadcrumb trail.
    """
    best: tuple[int, dict[str, Any]] | None = None
    for node in extract(html_text, base_url=base_url):
        rank = rank_of(node)
        if rank is None:
            continue
        if best is None or rank < best[0]:
            best = (rank, node)
    if best is None:
        return None
    return _to_business(best[1])


def _to_business(node: dict[str, Any]) -> Business:
    names = _type_names(node)
    return Business(
        schema_type=names[0] if names else "unknown",
        name=_text(node.get("name") or node.get("legalName")),
        description=_text(node.get("description")),
        url=_text(node.get("url")),
        telephones=_strings(node.get("telephone")),
        emails=tuple(e.removeprefix("mailto:") for e in _strings(node.get("email"))),
        address=_address(node.get("address")),
        same_as=_strings(node.get("sameAs")),
        opening_hours=_strings(node.get("openingHours") or node.get("openingHoursSpecification")),
        logo=_first_url(node.get("logo") or node.get("image")),
        rating=_rating(node.get("aggregateRating")),
        review_count=_count(node.get("aggregateRating")),
    )


def _text(value: Any) -> str | None:
    """One string from whatever shape a site used. Whitespace collapsed; empty becomes None."""
    if isinstance(value, list):
        for item in value:
            found = _text(item)
            if found:
                return found
        return None
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("@value") or value.get("value"))
    if value is None:
        return None
    collapsed = " ".join(str(value).split())
    return collapsed or None


def _strings(value: Any) -> tuple[str, ...]:
    """Every string in a field that may be one value, a list, or a list of objects."""
    if value is None:
        return ()
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            # openingHoursSpecification is an object per day; its text is not one string.
            text = _text(item.get("@value") or item.get("name") or item.get("value"))
            if text is None and item.get("dayOfWeek"):
                text = _specification(item)
        else:
            text = _text(item)
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _specification(item: dict[str, Any]) -> str | None:
    """`openingHoursSpecification` flattened to the string form `openingHours` would have used."""
    days = _strings(item.get("dayOfWeek"))
    opens, closes = _text(item.get("opens")), _text(item.get("closes"))
    if not days or not opens or not closes:
        return None
    short = ",".join(d.rsplit("/", 1)[-1][:2] for d in days)
    return f"{short} {opens}-{closes}"


def _address(value: Any) -> Address:
    if isinstance(value, list):
        return _address(value[0]) if value else Address()
    if isinstance(value, str):
        # A site that published its address as one string gets it kept as the street line rather
        # than split on commas: parsing it into components is task 3.12's job, with a real parser.
        return Address(street=_text(value))
    if not isinstance(value, dict):
        return Address()
    # Microdata nests the fields under `properties`; JSON-LD puts them on the node itself.
    nested = value.get("properties")
    properties: dict[str, Any] = nested if isinstance(nested, dict) else value
    country = _text(properties.get("addressCountry"))
    return Address(
        street=_text(properties.get("streetAddress")),
        locality=_text(properties.get("addressLocality")),
        region=_text(properties.get("addressRegion")),
        postal_code=_text(properties.get("postalCode")),
        # ISO-3166 alpha-2 per CLAUDE.md, but only when the site already published one: "India"
        # is a country name, not a code, and mapping it belongs with the other normalisers.
        country=country.upper() if country and re.fullmatch(r"[A-Za-z]{2}", country) else country,
    )


def _first_url(value: Any) -> str | None:
    found = _text(value if not isinstance(value, dict) else value.get("url") or value.get("@id"))
    return found if found and found.lower().startswith(("http://", "https://", "//")) else None


def _rating(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    raw = _text(value.get("ratingValue"))
    try:
        rating = float(raw) if raw else None
    except ValueError:
        return None
    # A site is free to publish 9/10. Out of range says the scale is not the one we store.
    return rating if rating is not None and 0.0 <= rating <= 5.0 else None


def _count(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    raw = _text(value.get("reviewCount") or value.get("ratingCount"))
    try:
        return int(float(raw)) if raw else None
    except ValueError:
        return None


def json_ld_blocks(html_text: str) -> list[Any]:
    """The raw `application/ld+json` payloads, one at a time, skipping any that is not JSON.

    Used as the fallback in `extract` and for looking at what a misbehaving site published.
    """
    blocks: list[Any] = []
    for match in re.finditer(
        r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_text
    ):
        try:
            blocks.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    return blocks
