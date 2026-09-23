"""Turning a ResearchSpec into the tasks that will answer it (docs/06 section 2, task 2.10).

The output is a list of `PlannedTask`, which the handler writes to `research_tasks` and fans out
as discovery envelopes. Nothing here touches the network, the database or Redis beyond reading
the seeded reference tables: given the same spec, budget, expansion and capability map it
produces the same plan, which is what makes it testable and lets a task's identity be a hash of
its input.

Three external facts shape everything below, and none of them is a choice:

* **The job's money is on the envelope, not in the spec.** `limits.max_credits` is the user's
  ceiling; the API reserves `min(credits_per_lead * max_results, max_credits)` and puts *that* on
  the envelope. Splitting the ceiling would hand out credits nobody reserved.
* **A budget is write-once.** `app_worker` has no UPDATE grant on `research_tasks.credit_budget`
  (migration 0013), so what a task gets at plan time is what it has. Nothing can top it up later,
  and until task 2.11 adds a job-wide ledger the sum of these budgets *is* the job's only ceiling.
* **A plan is a tree, not a DAG.** `research_tasks.parent_task_id` is a single parent within one
  job; fan-in would need an edge table (`db/schema/research.ts`). Version 1 plans a flat list, so
  the tree machinery is not built here — it arrives with the first task that needs a parent.
"""

from dataclasses import dataclass, field
from math import ceil
from typing import Any, Final

from leadforge_contracts.research_spec import ResearchSpec

from app.db.reference import GeoArea, ReferenceData
from app.planner.capability import Capabilities
from app.planner.templates import PlanTemplate

#: What each discovery source's task is called. `research_tasks.type` must match
#: `^[a-z][a-z0-9_]*\\.[a-z][a-z0-9_]*$` (a CHECK constraint, and the same shape as an envelope
#: type). A source with no entry here cannot be planned for, which is reported rather than
#: silently skipped: it means the registry grew and the planner did not.
TASK_TYPE_BY_SOURCE: Final[dict[str, str]] = {
    "google_places": "discovery.places_text_search",
}

#: The most results one task may ask a source for. Above this a single call stops being one call:
#: the Places connector pages and tiles internally, and each page is billed.
MAX_RESULTS_PER_TASK: Final[int] = 200

#: Below this a task cannot buy anything useful, so the plan drops it rather than queueing work
#: that will stop on `budget_exhausted` after its first call.
MIN_TASK_CREDITS: Final[int] = 1


def text_of(value: object) -> str:
    """The string inside a contract value.

    The generated Pydantic models wrap every constrained string — a city, a taxonomy id, an
    industry term — in a `RootModel`, whose `str()` is its repr: `str(city)` gives
    `"root='Pune'"`, not `"Pune"`. Looking a place up under that name finds nothing, so every
    search in the job would silently lose its geography.
    """
    return str(getattr(value, "root", value))


@dataclass(frozen=True, slots=True)
class PlannedTask:
    """One row-to-be in `research_tasks`, plus what the executor needs to run it."""

    type: str
    input: dict[str, Any]
    credit_budget: int


@dataclass(frozen=True, slots=True)
class Plan:
    tasks: list[PlannedTask] = field(default_factory=list)
    #: Fields nothing registered can fill. The job reports these rather than silently omitting
    #: columns the user asked for and paid for (docs/06 section 9).
    unsatisfiable: tuple[str, ...] = ()
    #: Places named in the spec that the geo seed does not know. Same reasoning: a guessed
    #: bounding box would search the wrong part of the world.
    unknown_places: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def total_credits(self) -> int:
        return sum(t.credit_budget for t in self.tasks)


@dataclass(frozen=True, slots=True)
class Expansion:
    """What `query_expand` returned, already clamped to the caller's limits."""

    queries: tuple[str, ...]
    sub_localities: tuple[str, ...] = ()


async def build_plan(
    spec: ResearchSpec,
    *,
    capabilities: Capabilities,
    template: PlanTemplate,
    reference: ReferenceData,
    expansion: Expansion,
    credits: int,
) -> Plan:
    """The plan for one spec, inside `credits` reserved credits.

    `credits` is `budget.credits_remaining` from the job envelope — what the API actually took
    from the ledger — never `spec.limits.max_credits`, which is only the user's ceiling and is
    routinely higher than the reservation.
    """
    source = _discovery_source(capabilities)
    if source is None:
        # Every requested field is either unfillable or only fillable about a business someone
        # already named. There is nothing to discover, and saying so beats an empty job.
        return Plan(
            unsatisfiable=capabilities.unsatisfiable,
            notes=(_no_source_note(capabilities),),
        )

    areas, unknown = await _resolve_places(spec, reference)
    if not areas:
        return Plan(
            unsatisfiable=capabilities.unsatisfiable,
            unknown_places=unknown,
            notes=("no searchable place in this request",),
        )

    google_types = await _google_types(spec, reference)
    searches, dropped = _searches(
        spec, template=template, expansion=expansion, areas=areas, google_types=google_types
    )
    budgets = _split_budget(credits, template=template, count=len(searches))
    per_task_results = _results_per_task(spec, count=len(searches))
    tasks = [
        PlannedTask(
            type=TASK_TYPE_BY_SOURCE[source],
            input=_with_limits(search, source=source, max_results=per_task_results),
            credit_budget=budget,
        )
        for search, budget in zip(searches, budgets, strict=True)
        if budget >= MIN_TASK_CREDITS
    ]

    notes: list[str] = []
    if dropped:
        notes.append(
            f"{dropped} searches not planned: this request needs more than the "
            f"{template.max_discovery_tasks} a {template.intent} job may run"
        )
    if len(tasks) < len(searches):
        notes.append(
            f"{len(searches) - len(tasks)} searches dropped: {credits} credits could not fund them"
        )
    return Plan(
        tasks=tasks,
        unsatisfiable=capabilities.unsatisfiable,
        unknown_places=unknown,
        notes=tuple(notes),
    )


def _discovery_source(capabilities: Capabilities) -> str | None:
    """The cheapest enabled source that can find businesses *and* that we know how to task.

    Both halves matter. `discovery_sources()` already excludes sources that can only answer about
    a business someone named (`discovers`, docs/08). This then skips any the planner has no task
    type for, rather than emitting a task the executor cannot run.
    """
    return next((s for s in capabilities.discovery_sources() if s in TASK_TYPE_BY_SOURCE), None)


def _no_source_note(capabilities: Capabilities) -> str:
    unknown = [s for s in capabilities.discovery_sources() if s not in TASK_TYPE_BY_SOURCE]
    if unknown:
        # The registry grew and this did not. Worth naming: it is our bug, not the user's request.
        return f"no planner task type for the available discovery sources: {', '.join(unknown)}"
    return "no enabled source can discover businesses for this request"


async def _resolve_places(
    spec: ResearchSpec, reference: ReferenceData
) -> tuple[list[GeoArea], tuple[str, ...]]:
    """Every place the spec names, as boxes we can search, plus the ones we cannot.

    Deduplicated by the area, not by the words used: "Pune" and "Poona" are one place, and
    planning both would spend a second budget searching the same ground — money the plan believed
    it had allocated and that nothing downstream could reclaim, because two identical task inputs
    collide on `task_key` and the second insert quietly returns the first one's row.
    """
    location = spec.filters.location
    if location is None:
        return [], ()
    country = location.country
    named = [text_of(p) for p in (location.cities or [])] + [
        text_of(p) for p in (location.states or [])
    ]
    by_slug: dict[str, GeoArea] = {}
    unknown: list[str] = []
    for name in named:
        area = await reference.find_area(name, country=country)
        if area is None:
            unknown.append(name)
        else:
            by_slug.setdefault(_area_key(area), area)
    if not named and country:
        # A country-wide request is legitimate; it is simply one very large box.
        whole = await reference.find_area(country, country=country)
        if whole is not None:
            by_slug.setdefault(_area_key(whole), whole)
    return list(by_slug.values()), tuple(unknown)


def _area_key(area: GeoArea) -> str:
    return f"{area.country}:{area.kind}:{area.slug}"


async def _google_types(spec: ResearchSpec, reference: ReferenceData) -> tuple[str, ...]:
    industry = spec.filters.industry
    if industry is None or not industry.taxonomy_ids:
        return ()
    return await reference.google_types_for([text_of(t) for t in industry.taxonomy_ids])


def _searches(
    spec: ResearchSpec,
    *,
    template: PlanTemplate,
    expansion: Expansion,
    areas: list[GeoArea],
    google_types: tuple[str, ...],
) -> tuple[list[dict[str, Any]], int]:
    """One search per (place, phrase), tiled by sub-locality where the template says to.

    Ordered so the most valuable searches come first: the plain phrase in each named place before
    any sub-locality, because a budget or a cap that runs out should run out on the long tail.
    Returns the searches that fit and how many were cut, so the job can say what it left out
    rather than quietly returning a fraction of the market.
    """
    category = google_types[0] if google_types else None
    searches: list[dict[str, Any]] = []
    for area in areas:
        for query in expansion.queries:
            searches.append(_search(query, area=area, category=category))
    if template.tile_sub_localities and expansion.sub_localities:
        # Only cities get tiled by name: a sub-locality of a state is just a city, and the
        # Places connector already tiles a large box geometrically.
        #
        # Each keeps the *city's* box rather than the locality's, because the geo seed holds
        # cities and states, not neighbourhoods, and a box invented for "Kothrud" would be a
        # guess about where Kothrud is. The locality narrows the search through the query text,
        # which is what a maps API is good at; the box only stops it leaving the city.
        head = expansion.queries[0]
        for area in (a for a in areas if a.is_city):
            for locality in expansion.sub_localities:
                searches.append(
                    _search(f"{head} {locality}", area=area, category=category, locality=locality)
                )
    dropped = max(0, len(searches) - template.max_discovery_tasks)
    return searches[: template.max_discovery_tasks], dropped


def _search(
    query: str,
    *,
    area: GeoArea,
    category: str | None,
    locality: str | None = None,
) -> dict[str, Any]:
    """The `input` of one discovery task, minus the limits the budget decides.

    Stored as jsonb and hashed into the task's identity, so it must contain no clock, no random
    value and nothing that differs between two runs of the same plan.
    """
    payload: dict[str, Any] = {
        "query": {
            "text": query,
            "bbox": list(area.bbox),
            "country": area.country,
        },
        "area_slug": area.slug,
    }
    if category:
        payload["query"]["category"] = category
    if locality:
        payload["locality"] = locality
    return payload


def _with_limits(search: dict[str, Any], *, source: str, max_results: int) -> dict[str, Any]:
    payload = {**search, "source": source, "query": {**search["query"]}}
    payload["query"]["max_results"] = max_results
    return payload


def _results_per_task(spec: ResearchSpec, *, count: int) -> int:
    """How many results one task may ask for, so the plan cannot outrun `max_results`.

    Credits are consumed per delivered candidate (docs/11), so giving every task the job's whole
    `max_results` is a second route past the reservation: a market map with 90 searches would ask
    for 90 times what the user said they wanted.
    """
    if count <= 0:
        return 0
    return max(1, min(MAX_RESULTS_PER_TASK, ceil(spec.limits.max_results / count)))


def _split_budget(credits: int, *, template: PlanTemplate, count: int) -> list[int]:
    """Divide the discovery share of the job's reserved credits across its searches.

    Even, with the remainder going to the earliest tasks, which are the ones most likely to
    return something. Even beats proportional here because nothing yet knows which search will
    yield more — `registry.providing` sorts by cost alone and there is no completed-job history
    to learn from, so a weighting would be a guess wearing arithmetic.

    Depth does not appear. It is tempting to let a "deep" job spend more, but the total is fixed
    by what was reserved, so the only thing depth can change is how that total is divided — and
    deep means *more per row*, which is less of it here and more for the stages after. Scaling
    the discovery share by depth did the opposite, and at `market_map` + `deep` handed discovery
    the entire budget, leaving Phase 3 enrichment permanently unfunded.
    """
    if count <= 0:
        return []
    pot = int(max(credits, 0) * min(template.discovery_share, 1.0))
    base, remainder = divmod(pot, count)
    return [base + (1 if i < remainder else 0) for i in range(count)]
