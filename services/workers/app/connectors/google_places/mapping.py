"""Places response -> FieldValue list, with provenance on every value (CLAUDE.md).

Pure and deterministic, so the recorded fixtures are a real test of it.

Two rules specific to this source:

* Nothing is invented. A field the mask did not return, or that the place does not have, produces
  no value at all — not an empty string, which would later look like a fact we checked.
* Every value carries `observed_at`, and everything here is perishable: Places content may be
  kept for 30 days (ADR-0011). The sweeper works from `observed_at`, so it must be the time the
  response was fetched, never the time a row happened to be written.
"""

from datetime import datetime
from typing import Any

from app.connectors.google_places.models import Place
from app.connectors.types import Candidate, FieldValue, RawResult

#: How much we trust a value from this source. Google's own data is good but not verified by us,
#: and a crawled value from the company's own site should win over it (docs/06 resolution).
CONFIDENCE: float = 0.85

#: Lower: a rating moves, and ours is a snapshot of the moment we asked.
VOLATILE_CONFIDENCE: float = 0.7

SOURCE_KEY = "google_places"


def place_url(place: Place) -> str:
    """Where a human can see this value, which is what provenance means (docs/06)."""
    return place.google_maps_uri or f"https://www.google.com/maps/place/?q=place_id:{place.id}"


def to_candidate(place: Place, observed_at: datetime) -> Candidate | None:
    """A discovery result. Without a name there is nothing to resolve against, so it is dropped."""
    name = place.display_name.text if place.display_name else None
    if not name:
        return None
    return Candidate(
        source_key=SOURCE_KEY,
        external_id=place.id,
        name=name,
        url=place.website_uri,
        phone=place.international_phone_number or place.national_phone_number,
        address=place.formatted_address,
        city=place.component("locality", "postal_town"),
        country=place.component_short("country"),
        lat=place.location.latitude if place.location else None,
        lng=place.location.longitude if place.location else None,
        source_url=place_url(place),
        observed_at=observed_at,
        raw={"place_id": place.id, "types": place.types},
    )


def to_field_values(place: Place, raw: RawResult) -> list[FieldValue]:
    """Everything this place tells us, each value standing on its own evidence."""
    url = place_url(place)
    values: list[FieldValue] = []

    def add(field: str, value: Any, confidence: float = CONFIDENCE) -> None:
        if value in (None, "", [], {}):
            return
        values.append(
            FieldValue(
                entity_type="company",
                field=field,
                value=value,
                source_key=SOURCE_KEY,
                source_url=url,
                observed_at=raw.fetched_at,
                method="api",
                confidence=confidence,
            )
        )

    add("name", place.display_name.text if place.display_name else None)
    add("category", _category(place))
    add("address", place.formatted_address)
    add("city", place.component("locality", "postal_town"))
    add("state", place.component("administrative_area_level_1"))
    add("country", place.component_short("country"))
    add("postal_code", place.component("postal_code"))
    add("phone", place.international_phone_number or place.national_phone_number)
    add("website", place.website_uri)
    add("google_maps_url", place.google_maps_uri)
    add("geo", _geo(place))
    add("rating", place.rating, VOLATILE_CONFIDENCE)
    add("review_count", place.user_rating_count, VOLATILE_CONFIDENCE)
    add("business_status", _business_status(place))
    add("opening_hours", _opening_hours(place), VOLATILE_CONFIDENCE)
    return values


def _category(place: Place) -> str | None:
    """The human-readable primary type where Google gives one, else the machine type."""
    if place.primary_type_display_name and place.primary_type_display_name.text:
        return place.primary_type_display_name.text
    return place.primary_type or (place.types[0] if place.types else None)


def _geo(place: Place) -> dict[str, float] | None:
    if place.location is None:
        return None
    return {"lat": place.location.latitude, "lng": place.location.longitude}


def _business_status(place: Place) -> str | None:
    """Google's enum, lowercased; `OPERATIONAL` becomes `operational` (docs/12 conventions)."""
    return place.business_status.lower() if place.business_status else None


def _opening_hours(place: Place) -> list[dict[str, Any]] | None:
    """Weekly periods only. `openNow` is true at the instant of the call and is not a fact."""
    hours = place.regular_opening_hours
    if hours is None or not hours.periods:
        return None
    periods = []
    for period in hours.periods:
        if period.open is None:
            continue
        entry: dict[str, Any] = {
            "day": period.open.day,
            "open": f"{period.open.hour:02d}:{period.open.minute:02d}",
        }
        if period.close is not None:
            entry["close"] = f"{period.close.hour:02d}:{period.close.minute:02d}"
        periods.append(entry)
    return periods or None
