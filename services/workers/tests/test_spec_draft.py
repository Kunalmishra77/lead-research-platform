"""Turning a parsed draft into a ResearchSpec (app/ai/spec_draft.py).

This is the half of `spec_parse` a model may not decide: defaults, limits and what gets searched.
Every spec built here is validated against the real contract schema, because an invalid spec
would only surface when a job is already running and credits are already reserved.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.ai.spec_draft import (
    CREDITS_PER_RESULT,
    DEFAULT_FIELDS,
    DEFAULT_MAX_RESULTS,
    MAX_RESULTS_CEILING,
    UNLOCATED_MAX_RESULTS,
    build_spec,
    needs_confirmation,
)

CONTRACT = (
    Path(__file__).resolve().parents[3] / "packages/contracts/schemas/research-spec.schema.json"
)
SPEC_VALIDATOR = Draft202012Validator(json.loads(CONTRACT.read_text(encoding="utf-8")))


def draft(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent": "prospecting",
        "industry_terms": ["restaurant"],
        "location": {"country": "in", "states": [], "cities": ["Delhi"], "radius_km": None},
        "employee_count": {"gte": None, "lte": None},
        "has_website": True,
        "keywords": {"must": [], "should": [], "not": []},
        "fields": [],
        "depth": None,
        "max_results": None,
        "exclude_existing": False,
        "seed_company": None,
        "unsupported": [],
        "ambiguities": [],
        "confidence": 0.95,
    }
    base.update(overrides)
    return base


def test_the_built_spec_satisfies_the_contract() -> None:
    SPEC_VALIDATOR.validate(build_spec(draft()))


@pytest.mark.parametrize("intent", sorted(DEFAULT_FIELDS))
def test_every_intent_builds_a_valid_spec(intent: str) -> None:
    SPEC_VALIDATOR.validate(build_spec(draft(intent=intent)))


def test_what_the_user_said_reaches_the_filters() -> None:
    spec = build_spec(draft())

    assert spec["intent"] == "prospecting"
    assert spec["filters"]["industry"]["include"] == ["restaurant"]
    assert spec["filters"]["location"]["cities"] == ["Delhi"]
    assert spec["filters"]["has_website"] is True
    # ISO-3166 alpha-2 is uppercase whatever the model wrote.
    assert spec["filters"]["location"]["country"] == "IN"


def test_what_the_user_did_not_say_becomes_no_filter_at_all() -> None:
    spec = build_spec(
        draft(
            industry_terms=[],
            has_website=None,
            location={"country": None, "states": [], "cities": [], "radius_km": None},
        )
    )

    # An empty filter is not a filter for nothing; it must be absent.
    assert spec["filters"] == {}
    assert "keywords" not in spec
    assert "exclude" not in spec


def test_the_defaults_for_the_intent_come_first_and_the_users_columns_follow() -> None:
    spec = build_spec(draft(fields=["email", "name", "instagram"]))

    assert spec["fields"][: len(DEFAULT_FIELDS["prospecting"])] == list(
        DEFAULT_FIELDS["prospecting"]
    )
    assert spec["fields"][-2:] == ["email", "instagram"]
    # "name" was already a default; asking for it again must not duplicate the column.
    assert len(spec["fields"]) == len(set(spec["fields"]))


def test_depth_defaults_per_intent_and_the_user_can_override_it() -> None:
    assert build_spec(draft())["depth"] == "standard"
    assert build_spec(draft(intent="single_company"))["depth"] == "deep"
    assert build_spec(draft(depth="quick"))["depth"] == "quick"


def test_the_result_ceiling_follows_the_depth_unless_the_user_gave_a_number() -> None:
    assert build_spec(draft())["limits"]["max_results"] == DEFAULT_MAX_RESULTS["standard"]
    assert build_spec(draft(depth="quick"))["limits"]["max_results"] == DEFAULT_MAX_RESULTS["quick"]
    assert build_spec(draft(max_results=37))["limits"]["max_results"] == 37


def test_an_absurd_number_is_clamped_to_the_contract_ceiling() -> None:
    spec = build_spec(draft(max_results=99_999_999))
    assert spec["limits"]["max_results"] == MAX_RESULTS_CEILING
    SPEC_VALIDATOR.validate(spec)


def test_one_named_company_is_one_result_however_it_was_asked_for() -> None:
    spec = build_spec(draft(intent="single_company", max_results=500))
    assert spec["limits"]["max_results"] == 1


def test_the_credit_cap_follows_the_result_count_and_the_depth() -> None:
    spec = build_spec(draft(depth="deep", max_results=100))
    assert spec["limits"]["max_credits"] == 100 * CREDITS_PER_RESULT["deep"]
    # A deeper search costs more per result, so the same count cannot cost the same.
    quick = build_spec(draft(depth="quick", max_results=100))
    assert quick["limits"]["max_credits"] < spec["limits"]["max_credits"]


def test_keywords_and_exclusions_are_carried_over_when_present() -> None:
    spec = build_spec(
        draft(
            keywords={"must": ["Korean"], "should": [" "], "not": ["franchise"]},
            exclude_existing=True,
        )
    )

    assert spec["keywords"] == {"must": ["Korean"], "not": ["franchise"]}
    assert spec["exclude"] == {"existing_leads": True}
    SPEC_VALIDATOR.validate(spec)


def test_blank_and_duplicate_values_are_cleaned_up() -> None:
    spec = build_spec(
        draft(
            industry_terms=["restaurant", " ", "Restaurant", "cafe"],
            location={
                "country": "IN",
                "states": [],
                "cities": ["Delhi", "delhi", " ", "Noida"],
                "radius_km": None,
            },
        )
    )

    assert spec["filters"]["industry"]["include"] == ["restaurant", "cafe"]
    assert spec["filters"]["location"]["cities"] == ["Delhi", "Noida"]


def test_an_employee_range_is_carried_only_where_it_was_given() -> None:
    assert "employee_count" not in build_spec(draft())["filters"]
    spec = build_spec(draft(employee_count={"gte": 50, "lte": None}))
    assert spec["filters"]["employee_count"] == {"gte": 50}
    SPEC_VALIDATOR.validate(spec)


def test_building_a_spec_does_not_touch_the_draft() -> None:
    original = draft()
    snapshot = json.dumps(original, sort_keys=True)
    build_spec(original)
    assert json.dumps(original, sort_keys=True) == snapshot


def test_a_clear_request_runs_without_asking() -> None:
    assert needs_confirmation(draft()) is False


def test_the_user_is_asked_before_credits_are_spent_on_a_guess() -> None:
    # Any one of these means a wrong guess would cost the user money for nothing.
    assert needs_confirmation(draft(ambiguities=["Which Springfield?"])) is True
    assert needs_confirmation(draft(confidence=0.2)) is True
    assert (
        needs_confirmation(
            draft(
                industry_terms=[],
                keywords={"must": [], "should": [], "not": []},
                location={"country": None, "states": [], "cities": [], "radius_km": None},
            )
        )
        is True
    )


def test_a_place_with_no_business_type_still_runs() -> None:
    # "everything in Pune" is searchable; the planner widens it.
    assert needs_confirmation(draft(industry_terms=[])) is False


def test_a_search_with_nowhere_to_look_is_held_however_sure_the_model_sounds() -> None:
    nowhere = draft(
        location={"country": None, "states": [], "cities": [], "radius_km": None},
        confidence=0.99,
    )
    # "plumbers", worldwide. The model's own confidence must not be the only guard on the
    # user's balance, so this is held deterministically.
    assert needs_confirmation(nowhere) is True
    # And if it were run anyway, it cannot quietly bill for 250 results.
    assert build_spec(nowhere)["limits"]["max_results"] == UNLOCATED_MAX_RESULTS


def test_a_named_company_is_enough_to_act_on_without_a_place() -> None:
    seeded = draft(
        intent="single_company",
        industry_terms=[],
        location={"country": None, "states": [], "cities": [], "radius_km": None},
        seed_company={"name": "Infosys", "website": None},
    )
    assert needs_confirmation(seeded) is False
    assert build_spec(seeded)["seed_company"] == {"name": "Infosys"}


def test_a_constraint_we_cannot_apply_is_confirmed_rather_than_ignored() -> None:
    # The user asked for a 4-star rating; charging them for unfiltered results is not an option.
    assert needs_confirmation(draft(unsupported=["rating above 4 stars"])) is True


# Every draft below is valid against app/ai/schemas/spec_parse.json yet would have produced a
# ResearchSpec the contract rejects. They are the reason this module distrusts its input.
ADVERSARIAL: dict[str, dict[str, Any]] = {
    "radius of zero": {
        "location": {"country": "IN", "states": [], "cities": ["Pune"], "radius_km": 0}
    },
    "industry term longer than the contract allows": {"industry_terms": ["a" * 400]},
    "city name longer than the contract allows": {
        "location": {"country": "IN", "states": [], "cities": ["c" * 400], "radius_km": None}
    },
    "keyword longer than the contract allows": {
        "keywords": {"must": ["k" * 400], "should": [], "not": []}
    },
    "country that is not an ISO code": {
        "location": {"country": "i1", "states": [], "cities": [], "radius_km": None}
    },
    "result count past the ceiling": {"max_results": 99_999_999},
    "employee range the wrong way round": {"employee_count": {"gte": 500, "lte": 10}},
    "company name longer than the contract allows": {
        "seed_company": {"name": "n" * 500, "website": "w" * 500}
    },
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_a_hostile_but_schema_valid_draft_still_builds_a_valid_spec(name: str) -> None:
    spec = build_spec(draft(**ADVERSARIAL[name]))
    SPEC_VALIDATOR.validate(spec)


def test_the_employee_range_is_read_the_way_the_user_meant_it() -> None:
    spec = build_spec(draft(employee_count={"gte": 500, "lte": 10}))
    # 500..10 matches nothing, and the job would spend credits proving that.
    assert spec["filters"]["employee_count"] == {"gte": 10, "lte": 500}


def test_a_country_that_is_not_an_iso_code_is_dropped_not_guessed() -> None:
    spec = build_spec(
        draft(location={"country": "india", "states": [], "cities": ["Pune"], "radius_km": None})
    )
    assert "country" not in spec["filters"]["location"]
    assert spec["filters"]["location"]["cities"] == ["Pune"]
