"""A row from the place index -> a candidate and its field values, with provenance on each.

Pure and deterministic, which is what makes the fixtures a real test of it.

Three rules specific to these sources:

* Nothing is invented. A field the dataset does not carry produces no value at all, never an
  empty string, which would later read as a fact somebody checked.
* `method` is `provider`: this is a third-party dataset, not a call we made and not something a
  model produced (CLAUDE.md).
* `observed_at` is when the dataset last touched the record, falling back to the release date --
  never the moment we read the file. A snapshot taken in September is September's observation
  however long the index sits on disk, and the freshness a user sees has to say so.
"""

from datetime import datetime

from app.connectors.types import Candidate, FieldValue
from app.crawl.contacts import social_from_url
from app.normalize.emails import is_role as is_role_email
from app.normalize.emails import normalize as normalize_email
from app.places.model import OVERTURE, Place

#: How much a value from a bulk dataset is trusted.
#:
#: The same as Google Places (0.85), and deliberately not higher despite the better coverage:
#: neither is verified by us, and a value crawled from the company's own site should still win
#: over both (docs/06 resolution).
#:
#: Not scaled by the record's own `confidence`, which was the obvious thing to do and is wrong.
#: Measured over 269,309 Delhi places, Overture's confidence does not predict contact quality at
#: all -- the 0.2 band carries a phone number 82% of the time and an email 64% of the time,
#: against 95% and 39% for the 1.0 band. It appears to express how many sources agree a place
#: exists, not how good its details are. Multiplying by it would have marked perfectly good
#: phone numbers as doubtful and made resolution prefer a worse source.
CONFIDENCE: float = 0.85

#: Lower, because a snapshot's category is the dataset's opinion of what a business is, not the
#: business's own description of itself.
CATEGORY_CONFIDENCE: float = 0.7


def to_candidate(place: Place, *, released_at: datetime | None = None) -> Candidate:
    """One discovery result, carrying the values the same read already produced."""
    observed_at = place.updated_at or released_at
    return Candidate(
        source_key=place.source,
        external_id=place.external_id,
        name=place.name,
        url=place.website,
        phone=place.phone,
        address=_address(place),
        city=place.city,
        country=place.country,
        lat=place.lat,
        lng=place.lon,
        source_url=place.source_url,
        observed_at=observed_at,
        raw={"category": place.category, "categories": list(place.categories)},
        # The index read already produced every value below, so nothing downstream has to fetch
        # the record again to write them down with provenance.
        values=tuple(to_field_values(place, released_at=released_at)),
    )


def to_field_values(place: Place, *, released_at: datetime | None = None) -> list[FieldValue]:
    """Everything this record states, each value standing on its own evidence."""
    observed_at = place.updated_at or released_at
    if observed_at is None:
        # Provenance requires a time (CLAUDE.md), and a dataset with neither a record timestamp
        # nor a release date cannot supply one. Refusing beats inventing `now()`, which would
        # claim a decade-old record was observed today.
        return []

    values: list[FieldValue] = []

    def add(field: str, value: object, confidence: float = CONFIDENCE) -> None:
        if value in (None, "", [], {}):
            return
        values.append(
            FieldValue(
                entity_type="company",
                field=field,
                value=value,
                source_key=place.source,
                source_url=place.source_url,
                observed_at=observed_at,
                method="provider",
                # The dataset states it; no rule of ours derived it (docs/09 provenance badges).
                derivation="found",
                confidence=confidence,
            )
        )

    add("name", place.name)
    add("category", place.category, CATEGORY_CONFIDENCE)
    add("address", _address(place))
    add("city", place.city)
    add("state", place.region)
    add("country", place.country)
    add("postal_code", place.postcode)
    add("phone", place.phone)
    add("website", place.website)
    # Normalised rather than passed through. The index's own check is that the value is
    # *plausible*; `field_values` is unique per (entity, field) for the current row, so
    # `Info@Clinic.IN` from a release and `info@clinic.in` from that clinic's own site are two
    # values for one mailbox -- the second retires the first and the first's provenance is lost.
    email = normalize_email(place.email)
    add("email", email)
    if email:
        # Computed here and stored, because docs/10 treats a named person's address as personal
        # data and a shared mailbox as not. The crawl has always worked this out and thrown it
        # away: 52 of the 424 addresses on the live graph are role accounts and nothing said so.
        add("email_role", "role" if is_role_email(email) else "person")
    add("geo", {"lat": place.lat, "lng": place.lon})
    if place.operating_status:
        add("business_status", place.operating_status.lower())
    for field, url in _socials(place):
        add(field, url)
    return values


def _socials(place: Place) -> list[tuple[str, str]]:
    """Each social URL under its own field, dropping anything that is not a profile.

    Classified by `app.crawl.contacts`, the same code the crawler uses, so a link found on a
    company's own site and one published in a dataset are filed identically. Overture's
    Facebook URLs are numeric page ids rather than vanity names, which the handle rules already
    accept.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url in place.socials:
        social = social_from_url(url)
        if social is None or social.platform in seen:
            continue
        seen.add(social.platform)
        found.append((str(social.platform), social.url))
    return found


def _address(place: Place) -> str | None:
    """The street address as one line, which is how every consumer of it wants it.

    Overture's `freeform` is already a line; OSM has a house number and a street that have to be
    joined. Neither carries the city, so it is not added here -- `city` is its own field and
    repeating it inside the address makes a CSV column that reads twice.
    """
    if place.street and place.housenumber:
        return f"{place.housenumber} {place.street}"
    return place.street or None


def released_at_of(source: str, extract_date: datetime | None) -> datetime | None:
    """The date a source's snapshot was cut, used when a record carries no time of its own.

    Overture publishes no per-record timestamp, so for it this is the only answer; OSM elements
    carry their own last-edited time and only fall back to this.
    """
    return extract_date if source == OVERTURE or extract_date is not None else None
