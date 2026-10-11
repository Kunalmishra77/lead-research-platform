"""One business, however it was found, and the rules that decide it is one.

Two datasets feed this: Overture Places, which is the discovery source, and OpenStreetMap, which
is the second opinion Phase 4 verifies against (ADR-0017). They describe the world differently --
OSM has primary tags, Overture has a category taxonomy -- so each reader maps into the single
`Place` below, and everything downstream reads one shape.

The business-or-not decision lives here because nothing downstream re-examines it: a car park
admitted at this line becomes a lead, gets crawled, and reaches a client. Getting it wrong is the
cheapest way to ruin lead quality.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

#: Where a place came from. Matches `app.sources.key`.
OVERTURE: Final[str] = "overture"
OSM: Final[str] = "osm"


@dataclass(frozen=True, slots=True)
class Place:
    """A business as a dataset records it, before any of our own normalisation."""

    source: str
    #: Stable within `source`: an Overture GERS id, or `n574533684` for an OSM node.
    external_id: str
    name: str
    #: The source's most specific category, kept raw (`leisure=fitness_centre`,
    #: `pizza_restaurant`) rather than mapped to our taxonomy, so the mapping can change without
    #: re-reading gigabytes.
    category: str
    #: Every category this place belongs to, including `category` itself. Overture publishes a
    #: hierarchy (`food_and_drink` > `restaurant` > `indian_restaurant`); OSM has one level, so
    #: for it this holds the single category.
    #:
    #: Searching all of them rather than the leaf is what makes a request for restaurants return
    #: the Indian, Chinese and pizza ones too: 4,276 Delhi places carry `restaurant` as their
    #: leaf and 11,631 carry it somewhere in their hierarchy.
    #:
    #: A set, not a path: read back from the index it comes in whatever order SQLite returns,
    #: because nothing needs the ordering and storing a position column to preserve an order no
    #: caller reads is a column that can only ever go stale. The most specific one is in
    #: `category`, which is the only level anything displays.
    categories: tuple[str, ...]
    lat: float
    lon: float
    #: Where a person can check this record. Overture publishes no per-record page, so for it
    #: this is the release object the row came from -- exact, if not clickable.
    source_url: str = ""
    phone: str | None = None
    website: str | None = None
    email: str | None = None
    socials: tuple[str, ...] = ()
    street: str | None = None
    housenumber: str | None = None
    city: str | None = None
    postcode: str | None = None
    region: str | None = None
    country: str | None = None
    #: The source's own confidence, 0-1, where it publishes one.
    confidence: float | None = None
    #: Overture's `operating_status`. A closed business with a perfect phone number is a worse
    #: lead than no lead, so this is carried rather than dropped.
    operating_status: str | None = None
    #: When the source last touched this record -- the freshness every value from it inherits.
    #: Not when we read the file.
    updated_at: datetime | None = None

    @property
    def is_contactable(self) -> bool:
        return bool(self.phone or self.email)


# --------------------------------------------------------------------------------------------
# OpenStreetMap tag rules
# --------------------------------------------------------------------------------------------

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
#:
#: The test of membership is whether anything could ever be sold to it, which is narrower than
#: "is it a business". A temple buys sound systems and catering, and a petrol pump is a company,
#: so both stay in; an ATM is a machine belonging to a bank that is already indexed separately,
#: so it does not. Keeping the list to that test is what stops it quietly deciding which
#: customers our users are allowed to find. `amenity=atm` alone was 1,301 rows of the 76,153 in
#: the first real index, every one of them unreachable.
NOT_A_BUSINESS_AMENITY: Final[frozenset[str]] = frozenset(
    {
        "atm",
        "bench",
        "bicycle_parking",
        "bicycle_repair_station",
        "bus_station",
        "charging_station",
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
        "vending_machine",
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


def category_of(tags: Mapping[str, str]) -> str | None:
    """The `key=value` an OSM element is a business by, or None if it is not one."""
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


def osm_place(
    *,
    osm_type: str,
    osm_id: int,
    tags: Mapping[str, str],
    lat: float,
    lon: float,
    updated_at: datetime | None = None,
) -> Place | None:
    """Builds a `Place` from an OSM element, or None when it is not a named business."""
    name = name_of(tags)
    if name is None:
        return None
    category = category_of(tags)
    if category is None:
        return None
    kind = {"n": "node", "w": "way", "r": "relation"}[osm_type]
    return Place(
        source=OSM,
        external_id=f"{osm_type}{osm_id}",
        name=name,
        category=category,
        categories=(category,),
        lat=lat,
        lon=lon,
        source_url=f"https://www.openstreetmap.org/{kind}/{osm_id}",
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


# --------------------------------------------------------------------------------------------
# Overture category rules
# --------------------------------------------------------------------------------------------

#: Overture `basic_category` values that are machines or infrastructure. The same test as the OSM
#: list: could anything ever be sold to it.
#:
#: Measured on Delhi, these are 2,975 of 286,277 rows -- 1.0%. Small, and worth removing anyway:
#: an ATM carries its bank's national helpline, so leaving them in both pads the lead count and
#: pads the apparent phone coverage with a number nobody wants to be called on.
NOT_A_BUSINESS_OVERTURE: Final[frozenset[str]] = frozenset(
    {
        "atm",
        "bench",
        "bus_stop",
        "charging_station",
        "fuel_station_atm",
        "parking",
        "parking_lot",
        "post_box",
        "toilet",
        "vending_machine",
    }
)

#: Operating statuses that mean there is nobody there. Overture publishes this and it would be
#: negligent to ignore: a closed business with a perfect phone number is worse than no lead.
CLOSED_STATUSES: Final[frozenset[str]] = frozenset({"closed", "closed_permanently"})


def is_business(category: str | None, operating_status: str | None = None) -> bool:
    """Whether an Overture row is something a customer could be found in."""
    if not category:
        return False
    if category in NOT_A_BUSINESS_OVERTURE:
        return False
    return not (operating_status and operating_status.lower() in CLOSED_STATUSES)


#: The fewest digits anything dialable has. Indian landlines are 10 with an STD code; short codes
#: are not business numbers.
_MIN_PHONE_DIGITS: Final[int] = 7
_MAX_PHONE_DIGITS: Final[int] = 15  # E.164's ceiling


def looks_like_phone(value: str | None) -> bool:
    """Whether a value is plausibly a phone number at all.

    This exists because some upstream rows are column-shifted: a Delhi sample turned up a place
    whose phone was `IN-HR`, email `IN` and website `122004` -- a region code, a country and a
    postcode, each one field to the left of where it belonged. 1,327 of 226,918 Delhi phone
    values, 0.6%, fail this check.

    A junk value is worse than a missing one. Missing, and the lead is honestly incomplete;
    present, and someone dials a region code and concludes the product is broken. So the test
    runs at ingest and the value is dropped rather than stored and explained later.

    Deliberately loose -- digits, and the punctuation real numbers are written with. Deciding
    whether a well-formed number is correct for a country is `app/normalize`'s job, and doing it
    twice in two places is how the two come to disagree.
    """
    if not value:
        return False
    digits = sum(c.isdigit() for c in value)
    if not _MIN_PHONE_DIGITS <= digits <= _MAX_PHONE_DIGITS:
        return False
    return all(c.isdigit() or c in "+()- ./x," for c in value)


def looks_like_email(value: str | None) -> bool:
    """Whether a value is plausibly an email address.

    Checked for the same reason as the phone, and checked separately on purpose. The corrupted
    row found in Delhi had `IN-HR` in phone, `IN` in email and `122004` in website -- every field
    shifted one place. Validating only the phone and trusting the rest would have stored a
    postcode as a company's website.

    Plausible, not deliverable. Whether anyone answers is `app/verify`'s question.
    """
    if not value or len(value) > 254:
        return False
    local, _, domain = value.partition("@")
    if not local or not domain or "@" in domain:
        return False
    return "." in domain and not domain.startswith(".") and not domain.endswith(".")


def looks_like_url(value: str | None) -> bool:
    """Whether a value is plausibly a web address rather than a postcode that slid one column."""
    if not value:
        return False
    lowered = value.strip().lower()
    if lowered.startswith(("http://", "https://")):
        return "." in lowered.split("//", 1)[1]
    # Bare hosts appear in these datasets too; a dot and no spaces is the whole test.
    return "." in lowered and " " not in lowered and not lowered.replace(".", "").isdigit()
