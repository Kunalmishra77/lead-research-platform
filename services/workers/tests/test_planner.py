"""The planner (task 2.10, docs/06 section 2).

What is worth pinning here is not "a plan came out" but the four ways a plan costs the user
money or silently loses them results: a source that cannot discover being used for discovery, a
place we cannot locate being guessed at, a budget handed out twice, and a cancelled job that
keeps spending.
"""

from typing import Any

from app.connectors.registry import ConnectorRegistry
from app.db.reference import GeoArea
from app.planner.capability import Capabilities, FieldPlan, build_capabilities
from app.planner.plan import TASK_TYPE_BY_SOURCE, Expansion, build_plan
from app.planner.templates import TEMPLATES, template_for
from tests.planner_support import PUNE, FakeReference, make_registry, make_spec


def capabilities_for(fields: list[str], **kwargs: Any) -> Any:
    return build_capabilities(make_registry(**kwargs), fields)


# ---------------------------------------------------------------- the capability map


def test_a_source_that_cannot_find_businesses_is_not_offered_for_discovery() -> None:
    caps = capabilities_for(["website", "name"])

    # serp answers "what is this business's site"; it cannot answer "which businesses are
    # there". It is also the cheapest source, so a plain cheapest-first sort would put it at the
    # front of the discovery plan and the job would find nothing at all (docs/08).
    assert caps.by_field["website"].enrichment == ("serp", "google_places")
    assert caps.by_field["website"].discovery == ("google_places",)
    assert caps.discovery_sources() == ("google_places",)


def test_a_field_nothing_can_fill_is_named_rather_than_quietly_dropped() -> None:
    caps = capabilities_for(["name", "employee_band", "technologies"])

    # The user asked for these columns and is paying for the job. "We have no source for it" and
    # "we looked and found none" are different answers and only one of them is honest here.
    assert caps.unsatisfiable == ("employee_band", "technologies")
    assert caps.by_field["name"].satisfiable


def test_with_no_connectors_enabled_nothing_is_satisfiable() -> None:
    caps = build_capabilities(ConnectorRegistry(), ["name", "website"])
    assert caps.unsatisfiable == ("name", "website")
    assert caps.discovery_sources() == ()


# ---------------------------------------------------------------- places


async def test_a_place_the_seed_does_not_know_is_reported_not_guessed() -> None:
    plan = await build_plan(
        make_spec(cities=["Atlantis"]),
        capabilities=capabilities_for(["name"]),
        template=template_for("prospecting"),
        reference=FakeReference(),
        expansion=Expansion(queries=("dental clinics in Atlantis",)),
        credits=500,
    )

    # Inventing a bounding box would send every search in the job to the wrong part of the
    # world, and every result would arrive with provenance that looks perfectly credible.
    assert plan.tasks == []
    assert plan.unknown_places == ("Atlantis",)
    assert "no searchable place" in plan.notes[0]


async def test_a_known_city_becomes_a_box_the_connector_can_search() -> None:
    plan = await build_plan(
        make_spec(cities=["Pune"]),
        capabilities=capabilities_for(["name"]),
        template=template_for("prospecting"),
        reference=FakeReference(),
        expansion=Expansion(queries=("dental clinics in Pune",)),
        credits=500,
    )

    assert len(plan.tasks) == 1
    query = plan.tasks[0].input["query"]
    assert query["bbox"] == list(PUNE.bbox)
    assert query["country"] == "IN"
    assert plan.tasks[0].type == TASK_TYPE_BY_SOURCE["google_places"]
    assert plan.tasks[0].input["source"] == "google_places"
    assert plan.tasks[0].input["area_slug"] == PUNE.slug


async def test_the_industry_taxonomy_becomes_a_places_type() -> None:
    plan = await build_plan(
        make_spec(cities=["Pune"], taxonomy_ids=["dental-clinics"]),
        capabilities=capabilities_for(["name"]),
        template=template_for("prospecting"),
        reference=FakeReference(),
        expansion=Expansion(queries=("dental clinics in Pune",)),
        credits=500,
    )

    # Without it a text search drifts into anything whose page mentions teeth.
    assert plan.tasks[0].input["query"]["category"] == "dentist"


# ---------------------------------------------------------------- budgets


async def test_the_budget_split_is_the_credits_reserved_not_the_users_ceiling() -> None:
    # `limits.max_credits` is what the user said they would tolerate; the API reserves
    # min(credits_per_lead * max_results, max_credits) and puts THAT on the envelope. Splitting
    # the ceiling hands out credits nobody took from the ledger, and since credit_budget is
    # write-once (migration 0013) nothing downstream can claw it back.
    plan = await build_plan(
        make_spec(cities=["Pune"], max_credits=1000, max_results=500),
        capabilities=capabilities_for(["name"]),
        template=TEMPLATES["prospecting"],
        reference=FakeReference(),
        expansion=Expansion(queries=("a", "b", "c")),
        credits=500,
    )

    assert plan.total_credits == int(500 * TEMPLATES["prospecting"].discovery_share)
    assert plan.total_credits <= 500
    assert len(plan.tasks) == 3
    budgets = [t.credit_budget for t in plan.tasks]
    # The remainder goes to the earliest tasks, which are the ones most likely to return.
    assert budgets == sorted(budgets, reverse=True)
    assert max(budgets) - min(budgets) <= 1


async def test_no_template_and_depth_can_spend_more_than_was_reserved() -> None:
    # Depth used to multiply the discovery share, so market_map + deep reached 1.125 and was
    # clamped to 1.0: discovery took the entire budget and Phase 3 enrichment could never be
    # funded, because a task's budget cannot be topped up later.
    for name, template in TEMPLATES.items():
        for depth in ("quick", "standard", "deep"):
            plan = await build_plan(
                make_spec(cities=["Pune"], max_credits=10_000, depth=depth),
                capabilities=capabilities_for(["name"]),
                template=template,
                reference=FakeReference(),
                expansion=Expansion(queries=("a", "b")),
                credits=1000,
            )
            assert plan.total_credits <= 1000, (name, depth)
            # Something must be left for the stages after discovery.
            assert plan.total_credits < 1000, (name, depth)


async def test_searches_the_budget_cannot_fund_are_dropped_rather_than_queued() -> None:
    plan = await build_plan(
        make_spec(cities=["Pune"]),
        capabilities=capabilities_for(["name"]),
        template=TEMPLATES["prospecting"],
        reference=FakeReference(),
        expansion=Expansion(queries=("a", "b", "c", "d", "e", "f")),
        credits=2,
    )

    # A task with nothing to spend would start, make one call and stop on budget_exhausted,
    # which reads to the user as a failure rather than as a budget that was always too small.
    assert all(t.credit_budget >= 1 for t in plan.tasks)
    assert len(plan.tasks) < 6
    # Said as a flag, not as a phrase: the handler classifies the failure from this, and reading
    # it back out of prose would make the error class depend on the wording.
    assert plan.budget_limited
    assert plan.notes


async def test_a_task_is_funded_enough_to_make_one_call_of_its_source() -> None:
    # A live Delhi run planned 18 tasks at 3 credits each and every one was refused: one Places
    # search costs 35_000 micros and 3 credits buys 60_000, which does not cover the two tiles
    # Delhi needs. $0.63 went out and no leads came back. Fewer, funded searches beat many
    # starved ones -- a starved task spends nothing, finds nothing, and reads as a broken job.
    caps = capabilities_for(["name"])
    plan = await build_plan(
        make_spec(cities=["Pune"]),
        capabilities=caps,
        template=TEMPLATES["prospecting"],
        reference=FakeReference(),
        expansion=Expansion(queries=tuple(f"q{i}" for i in range(8))),
        credits=20,
        micros_per_credit=20_000,
    )

    per_call = caps.cost_by_source["google_places"]
    assert plan.tasks
    for task in plan.tasks:
        assert task.credit_budget * 20_000 >= per_call, "a task that cannot call buys nothing"
    assert len(plan.tasks) < 8
    assert plan.budget_limited


async def test_a_cheaper_rate_per_credit_funds_more_searches() -> None:
    async def count(micros_per_credit: int) -> int:
        plan = await build_plan(
            make_spec(cities=["Pune"]),
            capabilities=capabilities_for(["name"]),
            template=TEMPLATES["prospecting"],
            reference=FakeReference(),
            expansion=Expansion(queries=tuple(f"q{i}" for i in range(8))),
            credits=20,
            micros_per_credit=micros_per_credit,
        )
        return len(plan.tasks)

    # The rate is configurable on the API side, so the planner reads it from the envelope rather
    # than assuming 20_000. At twice the rate a credit buys twice the calls.
    assert await count(40_000) > await count(20_000)


async def test_a_market_map_spends_more_on_finding_rows_than_a_competitor_scan() -> None:
    assert TEMPLATES["market_map"].discovery_share > TEMPLATES["competitor_scan"].discovery_share
    # A market map is judged on completeness; a competitor scan on how well it knows a few.
    assert TEMPLATES["market_map"].tile_sub_localities
    assert not TEMPLATES["competitor_scan"].tile_sub_localities


async def test_a_discovery_source_the_planner_cannot_task_is_reported_not_ignored() -> None:
    caps = Capabilities(
        by_field={"name": FieldPlan(field="name", discovery=("some_new_source",), enrichment=())}
    )
    plan = await build_plan(
        make_spec(cities=["Pune"]),
        capabilities=caps,
        template=TEMPLATES["prospecting"],
        reference=FakeReference(),
        expansion=Expansion(queries=("a",)),
        credits=500,
    )

    # The registry grew and the planner did not. Emitting a task type the executor has never
    # heard of would fail later and look like the user's fault.
    assert plan.tasks == []
    assert "no planner task type" in plan.notes[0]


def test_an_intent_the_table_has_never_heard_of_still_plans() -> None:
    # ResearchSpec.intent is a closed enum, so this means the contract grew and this table did
    # not. Failing the job would punish the user for our omission.
    assert template_for("some_new_intent_from_a_later_phase").intent == "prospecting"


# ---------------------------------------------------------------- tiling and caps


async def test_a_city_is_covered_by_its_sub_localities() -> None:
    plan = await build_plan(
        make_spec(cities=["Pune"]),
        capabilities=capabilities_for(["name"]),
        template=TEMPLATES["prospecting"],
        reference=FakeReference(),
        expansion=Expansion(queries=("dental clinics",), sub_localities=("Kothrud", "Baner")),
        credits=500,
    )

    localities = [t.input.get("locality") for t in plan.tasks]
    assert localities == [None, "Kothrud", "Baner"]
    # The plain search comes first: a budget that runs out should run out on the long tail.
    assert plan.tasks[0].input["query"]["text"] == "dental clinics"
    assert "Kothrud" in plan.tasks[1].input["query"]["text"]


async def test_a_state_is_not_tiled_by_sub_locality() -> None:
    plan = await build_plan(
        make_spec(states=["Maharashtra"]),
        capabilities=capabilities_for(["name"]),
        template=TEMPLATES["prospecting"],
        reference=FakeReference(),
        expansion=Expansion(queries=("dental clinics",), sub_localities=("Kothrud",)),
        credits=500,
    )

    # A sub-locality of a state is just a city, and the Places connector already tiles a large
    # box geometrically. Doing it here as well would buy the same ground twice.
    assert len(plan.tasks) == 1


async def test_a_spec_naming_many_places_cannot_plan_unbounded_searches() -> None:
    template = TEMPLATES["prospecting"]
    # Three cities x 8 phrases, plus 8 phrases' worth of sub-locality tiles for each of them:
    # 264 searches against a cap of 60. The earlier version of this test named one city and
    # produced 48, so the cap it claimed to check never fired.
    plan = await build_plan(
        make_spec(cities=["Pune", "Mumbai", "Nagpur"], max_credits=100_000),
        capabilities=capabilities_for(["name"]),
        template=template,
        reference=FakeReference(),
        expansion=Expansion(
            queries=tuple(f"q{i}" for i in range(8)),
            sub_localities=tuple(f"area{i}" for i in range(80)),
        ),
        credits=100_000,
    )

    assert len(plan.tasks) == template.max_discovery_tasks
    # And it says what it left out, rather than quietly returning a slice of the market.
    assert any("not planned" in note for note in plan.notes)


async def test_one_place_named_twice_is_searched_once() -> None:
    plan = await build_plan(
        # "Poona" is an alias of "Pune" in the geo seed, so both resolve to the same box.
        make_spec(cities=["Pune", "Poona"]),
        capabilities=capabilities_for(["name"]),
        template=TEMPLATES["prospecting"],
        reference=FakeReference(),
        expansion=Expansion(queries=("dental clinics",)),
        credits=600,
    )

    # Two tasks with byte-identical input collide on `task_key`, so the second insert silently
    # returns the first one's row -- and the budget the plan believed it allocated to it is
    # forfeited, since nothing can move credits between tasks afterwards.
    assert len(plan.tasks) == 1
    assert plan.total_credits == int(600 * TEMPLATES["prospecting"].discovery_share)


async def test_the_job_cannot_ask_for_more_results_than_the_user_wanted() -> None:
    plan = await build_plan(
        make_spec(cities=["Pune"], max_results=100),
        capabilities=capabilities_for(["name"]),
        template=TEMPLATES["prospecting"],
        reference=FakeReference(),
        expansion=Expansion(queries=("a", "b", "c", "d")),
        credits=600,
    )

    asked = [t.input["query"]["max_results"] for t in plan.tasks]
    # Credits are consumed per delivered candidate (docs/11), so giving every task the whole
    # max_results is a second way past the reservation: four searches would fetch 400.
    assert sum(asked) <= 100 + len(plan.tasks)
    assert all(a >= 1 for a in asked)


# ---------------------------------------------------------------- nothing to do


async def test_a_job_with_no_discovery_source_says_so_instead_of_returning_nothing() -> None:
    caps = build_capabilities(make_registry(places=False), ["website"])
    plan = await build_plan(
        make_spec(cities=["Pune"]),
        capabilities=caps,
        template=template_for("prospecting"),
        reference=FakeReference(),
        expansion=Expansion(queries=("dental clinics in Pune",)),
        credits=500,
    )

    # Only serp is left, and it cannot find businesses. An empty job with no explanation looks
    # like "there are no dental clinics in Pune".
    assert plan.tasks == []
    assert "no enabled source can discover" in plan.notes[0]


def test_a_city_without_a_population_is_still_a_city() -> None:
    # population is nullable in the seed, and a missing number must not demote a place.
    assert GeoArea("x", "X", "city", "IN", (1.0, 2.0, 3.0, 4.0), None).is_city
