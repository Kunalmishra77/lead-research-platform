"""The seeded lookups the planner cannot work without (app/db/reference.py).

A wrong answer here is not a wrong answer, it is a search of the wrong place: the bounding box
these produce is what every discovery call in a job is aimed at. The cases below are the ones
where "close enough" is not.
"""

import pytest

from app.db.reference import GeoArea, Industry, ReferenceData, slugify

PUNE = GeoArea(
    slug="pune-maharashtra",
    name="Pune",
    kind="city",
    country="IN",
    bbox=(18.38, 73.71, 18.65, 73.99),
    population=3_115_431,
    aliases=frozenset({"pune", "poona"}),
)
DELHI_CITY = GeoArea(
    slug="delhi-delhi",
    name="Delhi",
    kind="city",
    country="IN",
    bbox=(28.4, 76.8, 28.9, 77.35),
    population=16_787_941,
    aliases=frozenset({"delhi", "new delhi"}),
)
DELHI_STATE = GeoArea(
    slug="delhi",
    name="Delhi",
    kind="state",
    country="IN",
    bbox=(28.4, 76.8, 28.9, 77.35),
    population=16_787_941,
    aliases=frozenset({"delhi", "nct of delhi"}),
)
SPRINGFIELD_BIG = GeoArea(
    slug="springfield-missouri",
    name="Springfield",
    kind="city",
    country="US",
    bbox=(37.0, -93.4, 37.3, -93.2),
    population=169_176,
    aliases=frozenset({"springfield"}),
)
SPRINGFIELD_SMALL = GeoArea(
    slug="springfield-vermont",
    name="Springfield",
    kind="city",
    country="US",
    bbox=(43.2, -72.6, 43.4, -72.4),
    population=9_062,
    aliases=frozenset({"springfield"}),
)
PUNE_US = GeoArea(
    slug="pune-nowhere",
    name="Pune",
    kind="city",
    country="US",
    bbox=(40.0, -75.0, 40.1, -74.9),
    population=1_000,
    aliases=frozenset({"pune"}),
)


class StubReference(ReferenceData):
    """ReferenceData with the two queries replaced, so the matching logic is what is tested."""

    def __init__(self, areas: tuple[GeoArea, ...], industries: dict[str, Industry]) -> None:
        super().__init__(engine=None)  # type: ignore[arg-type]
        self._areas = areas
        self._industries = industries


def ref(*areas: GeoArea, industries: dict[str, Industry] | None = None) -> StubReference:
    return StubReference(areas, industries or {})


def test_the_seed_slug_form_is_what_a_user_types_reduced() -> None:
    assert slugify("Pune") == "pune"
    assert slugify("  New   Delhi ") == "new-delhi"
    assert slugify("St. John's") == "st-john-s"
    assert slugify("") == ""
    assert slugify("!!!") == ""


async def test_a_place_is_found_by_the_name_rather_than_the_slug() -> None:
    # The seed's slugs are state-qualified ("pune-maharashtra"), so nothing a user types would
    # ever match one directly. Matching on the name and the aliases is the whole mechanism.
    found = await ref(PUNE).find_area("Pune", country="IN")
    assert found is PUNE
    assert await ref(PUNE).find_area("poona", country="IN") is PUNE
    assert await ref(PUNE).find_area("PUNE", country="IN") is PUNE


async def test_an_unknown_place_is_none_rather_than_a_near_miss() -> None:
    # None is a real answer the planner reports. Returning the closest thing in the seed would
    # aim every search in the job at a city the user never mentioned.
    assert await ref(PUNE).find_area("Atlantis", country="IN") is None
    assert await ref(PUNE).find_area("", country="IN") is None
    assert await ref(PUNE).find_area("   ", country="IN") is None


async def test_the_city_beats_the_state_of_the_same_name() -> None:
    found = await ref(DELHI_STATE, DELHI_CITY).find_area("Delhi", country="IN")
    # "restaurants in Delhi" means the city. Searching the state's box instead is a much larger
    # and much more expensive search than the user asked for.
    assert found is DELHI_CITY


async def test_the_larger_of_two_cities_sharing_a_name_wins() -> None:
    found = await ref(SPRINGFIELD_SMALL, SPRINGFIELD_BIG).find_area("Springfield", country="US")
    # Naming no state, a user almost always means the one people have heard of.
    assert found is SPRINGFIELD_BIG


async def test_the_country_decides_between_places_that_share_a_name() -> None:
    assert await ref(PUNE, PUNE_US).find_area("Pune", country="IN") is PUNE
    assert await ref(PUNE, PUNE_US).find_area("Pune", country="US") is PUNE_US
    # Without a country the population tie-break applies, which is the best available guess.
    assert await ref(PUNE, PUNE_US).find_area("Pune") is PUNE


async def test_a_place_with_no_recorded_population_still_matches() -> None:
    nameless = GeoArea(
        "x-y", "Xtown", "city", "IN", (1.0, 2.0, 3.0, 4.0), None, frozenset({"xtown"})
    )
    # population is nullable in the seed; a missing number must not exclude a place entirely.
    assert await ref(nameless).find_area("Xtown", country="IN") is nameless


async def test_industry_types_are_ordered_and_deduplicated() -> None:
    reference = ref(
        industries={
            "dental": Industry("dental", "Dental", ("dentist", "dental_clinic"), ()),
            "medical": Industry("medical", "Medical", ("doctor", "dentist"), ()),
        }
    )

    # Order matters: the planner sends the first as the Places `includedType`, and a set would
    # make which one that is depend on hash order.
    assert await reference.google_types_for(["dental", "medical"]) == (
        "dentist",
        "dental_clinic",
        "doctor",
    )
    assert await reference.google_types_for(["medical", "dental"]) == (
        "doctor",
        "dentist",
        "dental_clinic",
    )


async def test_an_industry_the_seed_does_not_know_contributes_nothing() -> None:
    reference = ref(industries={"dental": Industry("dental", "Dental", ("dentist",), ())})
    # An empty tuple means "search on the text alone", which is a worse search but a correct
    # one. Guessing a Places type would filter the results to the wrong kind of business.
    assert await reference.google_types_for(["not-a-real-slug"]) == ()
    assert await reference.google_types_for([]) == ()
    assert await reference.google_types_for(["not-real", "dental"]) == ("dentist",)


@pytest.mark.parametrize(
    ("kind", "is_city"),
    [("city", True), ("locality", True), ("state", False), ("country", False)],
)
def test_only_a_city_is_worth_tiling_by_name(kind: str, is_city: bool) -> None:
    # A sub-locality of a state is just a city, and the Places connector already tiles a large
    # box geometrically, so naming localities inside one buys the same ground twice.
    area = GeoArea("s", "N", kind, "IN", (1.0, 2.0, 3.0, 4.0), None)
    assert area.is_city is is_city
