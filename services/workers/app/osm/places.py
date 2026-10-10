"""What counts as a business in OpenStreetMap, and how its tags become one record.

Pure and deterministic, so the tag rules can be tested without a file: `place_from_tags` is the
only thing that decides whether an element is a lead at all. Getting that decision wrong is the
cheapest way to ruin lead quality, because every later stage trusts it.

OSM has no "is a business" flag. It has primary tags, and they divide cleanly in two:

* `shop`, `office`, `craft`, `healthcare` mean commerce by definition. Every value is a business.
* `amenity`, `leisure`, `tourism` are mixed. `amenity=restaurant` is a lead; `amenity=bench` is
  street furniture, and `leisure=park` is a park.

So the first group is allowed wholesale and the second is filtered by a deny list. The reverse --
an allow list for the mixed keys -- was rejected because OSM's value vocabulary is open-ended and
a list of permitted values silently drops every category nobody thought of, which for a
lead-generation tool means silently not finding customers.

A name is required on top of that. It is what makes a record a business someone can be contacted
about rather than a dot on a map, and it removes most street furniture on its own.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

#: Keys that make an element a candidate business, in the order they are tried. The first one
#: present becomes the category, so the order is a priority: a shop inside a hotel is a shop.
PRIMARY_KEYS: Final[tuple[str, ...]] = (
    "shop",
    "office",
    "craft",
    "healthcare",
    "amenity",
    "leisure",
    "tourism",
)

#: Keys whose every value is commerce, so no value filtering applies.
UNFILTERED_KEYS: Final[frozenset[str]] = frozenset({"shop", "office", "craft", "healthcare"})

#: `amenity` values that are infrastructure, not businesses. A named one is still not a lead:
#: "Gandhi Chowk Parking" has no one to sell to.
NOT_A_BUSINESS_AMENITY: Final[frozenset[str]] = frozenset(
    {
        "bench",
        "bicycle_parking",
        "bicycle_repair_station",
        "bus_station",
        "clock",
        "drinking_water",
        "fountain",
        "grave_yard",
        "hunting_stand",
        "parking",
        "parking_entrance",
        "parking_space",
        "public_bookcase",
        "recycling",
        "shelter",
        "telephone",
        "toilets",
        "waste_basket",
        "waste_disposal",
        "waste_transfer_station",
        "water_point",
    }
)

#: `leisure` values that are public space or a feature of one.
NOT_A_BUSINESS_LEISURE: Final[frozenset[str]] = frozenset(
    {
        "common",
        "dog_park",
        "firepit",
        "garden",
        "nature_reserve",
        "park",
        "picnic_table",
        "pitch",
        "playground",
        "slipway",
        "swimming_area",
        "track",
    }
)

#: `tourism` values that are a sign or a viewpoint rather than somewhere trading.
NOT_A_BUSINESS_TOURISM: Final[frozenset[str]] = frozenset(
    {
        "artwork",
        "attraction",
        "information",
        "picnic_site",
        "viewpoint",
    }
)

_DENIED: Final[Mapping[str, frozenset[str]]] = {
    "amenity": NOT_A_BUSINESS_AMENITY,
    "leisure": NOT_A_BUSINESS_LEISURE,
    "tourism": NOT_A_BUSINESS_TOURISM,
}

#: Tags carrying each contact value, in the order preferred. `contact:*` is the newer scheme and
#: both are in wide use, so both are read.
_NAME_TAGS: Final[tuple[str, ...]] = ("name", "name:en", "int_name", "brand")
_PHONE_TAGS: Final[tuple[str, ...]] = ("phone", "contact:phone", "contact:mobile", "mobile")
_WEBSITE_TAGS: Final[tuple[str, ...]] = ("website", "contact:website", "url")
_EMAIL_TAGS: Final[tuple[str, ...]] = ("email", "contact:email")


@dataclass(frozen=True, slots=True)
class Place:
    """One business as OpenStreetMap records it, before any of our own normalisation."""

    #: 'n', 'w' or 'r' -- the element kind, which with `osm_id` identifies it forever.
    osm_type: str
    osm_id: int
    name: str
    #: The primary tag as `key=value`, e.g. `leisure=fitness_centre`. Stored raw rather than
    #: mapped to our taxonomy so the mapping can change without re-reading a 1.6 GB file.
    category: str
    lat: float
    lon: float
    phone: str | None = None
    website: str | None = None
    email: str | None = None
    street: str | None = None
    housenumber: str | None = None
    city: str | None = None
    postcode: str | None = None
    #: The element's own last-edited time, which is how fresh this fact actually is. Not when we
    #: read the file: a POI untouched since 2019 should not look like today's observation.
    updated_at: datetime | None = None

    @property
    def osm_url(self) -> str:
        """The element's page, so any single value can be checked by a person."""
        kind = {"n": "node", "w": "way", "r": "relation"}[self.osm_type]
        return f"https://www.openstreetmap.org/{kind}/{self.osm_id}"


def category_of(tags: Mapping[str, str]) -> str | None:
    """The `key=value` this element is a business by, or None if it is not one."""
    for key in PRIMARY_KEYS:
        value = tags.get(key)
        if not value:
            continue
        if key in UNFILTERED_KEYS:
            return f"{key}={value}"
        if value in _DENIED.get(key, frozenset()):
            # A denied value is a decision, not a reason to keep looking: an element tagged
            # `amenity=parking` plus `leisure=pitch` is a car park next to a pitch either way.
            return None
        return f"{key}={value}"
    return None


def name_of(tags: Mapping[str, str]) -> str | None:
    """The business's name, or None. Whitespace-only names are treated as absent."""
    for key in _NAME_TAGS:
        value = tags.get(key, "").strip()
        if value:
            return value
    return None


def place_from_tags(
    *,
    osm_type: str,
    osm_id: int,
    tags: Mapping[str, str],
    lat: float,
    lon: float,
    updated_at: datetime | None = None,
) -> Place | None:
    """Builds a `Place`, or returns None when this element is not a named business."""
    name = name_of(tags)
    if name is None:
        return None
    category = category_of(tags)
    if category is None:
        return None
    return Place(
        osm_type=osm_type,
        osm_id=osm_id,
        name=name,
        category=category,
        lat=lat,
        lon=lon,
        phone=_first(tags, _PHONE_TAGS),
        website=_first(tags, _WEBSITE_TAGS),
        email=_first(tags, _EMAIL_TAGS),
        street=_first(tags, ("addr:street",)),
        housenumber=_first(tags, ("addr:housenumber",)),
        city=_first(tags, ("addr:city", "addr:town", "addr:village")),
        postcode=_first(tags, ("addr:postcode",)),
        updated_at=updated_at,
    )


def _first(tags: Mapping[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = tags.get(key, "").strip()
        if value:
            return value
    return None
