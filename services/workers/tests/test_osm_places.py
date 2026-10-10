"""The tag rules that decide whether an OSM element is a lead at all.

This is the cheapest place to ruin lead quality, because nothing downstream re-examines the
decision: a car park admitted here becomes a lead, gets crawled, and is exported to a client.
So the two halves of the rule are tested separately -- the keys that mean commerce outright, and
the mixed keys where a value has to be judged.
"""

from datetime import UTC, datetime

import pytest

from app.osm.places import Place, category_of, name_of, place_from_tags


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        # shop / office / craft / healthcare mean commerce whatever the value, including values
        # nobody has thought of yet -- which is the point of not using an allow list.
        ({"shop": "bakery"}, "shop=bakery"),
        ({"shop": "something_invented_last_tuesday"}, "shop=something_invented_last_tuesday"),
        ({"office": "lawyer"}, "office=lawyer"),
        ({"craft": "carpenter"}, "craft=carpenter"),
        ({"healthcare": "dentist"}, "healthcare=dentist"),
        # amenity / leisure / tourism are mixed: real businesses pass.
        ({"amenity": "restaurant"}, "amenity=restaurant"),
        ({"leisure": "fitness_centre"}, "leisure=fitness_centre"),
        ({"tourism": "hotel"}, "tourism=hotel"),
        # ... and infrastructure does not.
        ({"amenity": "parking"}, None),
        ({"amenity": "bench"}, None),
        ({"amenity": "waste_basket"}, None),
        # Machines, not businesses: an ATM belongs to a bank that is indexed in its own right.
        # 1,301 of the 76,153 rows in the first real index, none of them reachable.
        ({"amenity": "atm"}, None),
        ({"amenity": "vending_machine"}, None),
        ({"amenity": "charging_station"}, None),
        ({"leisure": "park"}, None),
        ({"leisure": "playground"}, None),
        ({"tourism": "viewpoint"}, None),
        # Nothing primary at all.
        ({"building": "yes"}, None),
        ({}, None),
        # An empty value is not a category.
        ({"shop": ""}, None),
    ],
)
def test_category_of(tags: dict[str, str], expected: str | None) -> None:
    assert category_of(tags) == expected


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        # The deny list asks whether anything could ever be sold to it, not whether the thing is
        # a business in the abstract. These all pass that test and must keep passing it: an
        # over-eager list silently decides which customers our users are allowed to find.
        ({"amenity": "place_of_worship"}, "amenity=place_of_worship"),
        ({"amenity": "fuel"}, "amenity=fuel"),
        ({"amenity": "school"}, "amenity=school"),
        ({"amenity": "college"}, "amenity=college"),
        ({"amenity": "marketplace"}, "amenity=marketplace"),
        ({"tourism": "camp_site"}, "tourism=camp_site"),
    ],
)
def test_things_that_can_be_sold_to_stay_in(tags: dict[str, str], expected: str) -> None:
    assert category_of(tags) == expected


def test_a_shop_inside_a_hotel_is_a_shop() -> None:
    """PRIMARY_KEYS order is a priority, not an arbitrary sequence."""
    assert category_of({"tourism": "hotel", "shop": "gift"}) == "shop=gift"


def test_a_denied_value_is_not_a_reason_to_keep_looking() -> None:
    """A car park beside a pitch is a car park, not a leisure business.

    Worth pinning down because the obvious implementation -- `continue` on a denied value -- would
    fall through to the next primary key and admit this element on `leisure=pitch`.
    """
    assert category_of({"amenity": "parking", "leisure": "pitch"}) is None


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"name": "Iron Gym"}, "Iron Gym"),
        ({"name:en": "Iron Gym"}, "Iron Gym"),
        ({"brand": "Cult Fit"}, "Cult Fit"),
        # `name` wins over the fallbacks.
        ({"name": "लोहा जिम", "name:en": "Iron Gym"}, "लोहा जिम"),
        # Whitespace is not a name.
        ({"name": "   "}, None),
        ({"name": "  Iron Gym  "}, "Iron Gym"),
        ({}, None),
    ],
)
def test_name_of(tags: dict[str, str], expected: str | None) -> None:
    assert name_of(tags) == expected


def test_a_business_with_no_name_is_not_a_lead() -> None:
    assert place_from_tags(osm_type="n", osm_id=1, tags={"shop": "bakery"}, lat=0, lon=0) is None


def test_contact_values_come_from_either_tagging_scheme() -> None:
    """Both `phone` and `contact:phone` are in wide use; reading one loses real contacts."""
    place = place_from_tags(
        osm_type="n",
        osm_id=7,
        tags={
            "name": "Iron Gym",
            "leisure": "fitness_centre",
            "contact:phone": "+91 98100 00000",
            "contact:website": "https://irongym.in",
            "contact:email": "hi@irongym.in",
        },
        lat=28.45,
        lon=77.10,
    )
    assert place is not None
    assert place.phone == "+91 98100 00000"
    assert place.website == "https://irongym.in"
    assert place.email == "hi@irongym.in"


def test_a_full_place_keeps_every_part_of_the_address() -> None:
    place = place_from_tags(
        osm_type="w",
        osm_id=42,
        tags={
            "name": "Fit Factory",
            "leisure": "fitness_centre",
            "phone": "+911141000000",
            "website": "https://fitfactory.in",
            "addr:housenumber": "12A",
            "addr:street": "MG Road",
            "addr:city": "Gurgaon",
            "addr:postcode": "122002",
        },
        lat=28.45,
        lon=77.10,
        updated_at=datetime(2026, 3, 4, tzinfo=UTC),
    )
    assert place == Place(
        osm_type="w",
        osm_id=42,
        name="Fit Factory",
        category="leisure=fitness_centre",
        lat=28.45,
        lon=77.10,
        phone="+911141000000",
        website="https://fitfactory.in",
        email=None,
        street="MG Road",
        housenumber="12A",
        city="Gurgaon",
        postcode="122002",
        updated_at=datetime(2026, 3, 4, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("osm_type", "expected"),
    [
        ("n", "https://www.openstreetmap.org/node/99"),
        ("w", "https://www.openstreetmap.org/way/99"),
        ("r", "https://www.openstreetmap.org/relation/99"),
    ],
)
def test_osm_url_points_at_the_element_a_person_can_check(osm_type: str, expected: str) -> None:
    """Provenance has to be openable. A client disputing one value gets this URL."""
    place = Place(osm_type=osm_type, osm_id=99, name="X", category="shop=bakery", lat=0.0, lon=0.0)
    assert place.osm_url == expected
