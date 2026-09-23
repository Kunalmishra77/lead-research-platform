"""Turning what the model read into a ResearchSpec (docs/07, packages/contracts).

The model's half of `spec_parse` is a reading of the user's words; this is the deterministic half
that makes a runnable spec out of it. The split matters for more than tidiness: defaults, credit
limits and field sets are policy, and policy that a model can invent is policy we do not control.

Everything here is pure, so the eval runner scores exactly what the parse endpoint will produce.

Nothing here trusts its input. The draft has been validated against `spec_parse.json`, but that
schema is the model's contract, not the ResearchSpec contract, and the two can drift. A spec that
violates `packages/contracts/schemas/research-spec.schema.json` would only surface once a job is
running and credits are already reserved, so every value is clamped on the way through.

The ceiling here is per spec, not per customer: `max_credits` can reach 40,000 from one sentence.
Whoever reserves credits (task 2.4) has to clamp again against the org's plan and balance.
"""

import re
from typing import Any, Final

SPEC_VERSION: Final = 1

#: ISO-3166 alpha-2, as the contract demands (`^[A-Z]{2}$`).
_COUNTRY = re.compile(r"^[A-Z]{2}$")

#: Columns every intent starts with; the user's own `fields` are added on top.
DEFAULT_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "prospecting": ("name", "category", "address", "city", "phone", "website"),
    "market_map": ("name", "category", "address", "city", "website", "rating", "review_count"),
    "competitor_scan": ("name", "category", "address", "city", "website", "rating"),
    "hiring_signal": ("name", "category", "city", "website", "hiring"),
    "single_company": ("name", "category", "address", "city", "phone", "website", "description"),
}

#: How hard to look when the user did not say.
DEFAULT_DEPTH: Final[dict[str, str]] = {
    "prospecting": "standard",
    "market_map": "standard",
    "competitor_scan": "standard",
    "hiring_signal": "standard",
    "single_company": "deep",
}

#: Result ceiling per depth when the user gave no number of their own.
DEFAULT_MAX_RESULTS: Final[dict[str, int]] = {"quick": 50, "standard": 250, "deep": 500}

#: Hard ceiling from the contract (ResearchSpec.limits.max_results).
MAX_RESULTS_CEILING: Final = 10_000

#: Credits per result we are willing to let one job reach before it pauses (docs/11).
CREDITS_PER_RESULT: Final[dict[str, int]] = {"quick": 1, "standard": 2, "deep": 4}
MAX_CREDITS_CEILING: Final = 1_000_000

#: A single company is one record however the request is phrased.
SINGLE_COMPANY_MAX_RESULTS: Final = 1

#: The contract caps every free-text value at 100 characters (ResearchSpec, `minLength: 1`).
MAX_TEXT_LENGTH: Final = 100
MAX_COMPANY_NAME_LENGTH: Final = 200
MAX_WEBSITE_LENGTH: Final = 300

#: How many results a search with no place at all may return before the user is asked first.
#: A worldwide "plumbers" is not a search; running it would spend credits proving that.
UNLOCATED_MAX_RESULTS: Final = 50


def build_spec(draft: dict[str, Any]) -> dict[str, Any]:
    """Expands a validated `spec_parse` draft into a ResearchSpec.

    The draft has already been checked against `app/ai/schemas/spec_parse.json`, so this reads
    its fields directly; anything the model left null or empty is simply omitted, which is how a
    filter stays absent rather than becoming a filter for "nothing".
    """
    intent = draft["intent"]
    depth = draft.get("depth") or DEFAULT_DEPTH[intent]
    max_results = _max_results(draft, intent, depth)

    spec: dict[str, Any] = {
        "spec_version": SPEC_VERSION,
        "entity": "company",
        "intent": intent,
        "filters": _filters(draft),
        "fields": _fields(draft, intent),
        "depth": depth,
        "limits": {
            "max_results": max_results,
            "max_credits": min(max_results * CREDITS_PER_RESULT[depth], MAX_CREDITS_CEILING),
        },
    }

    seed = _seed_company(draft.get("seed_company"))
    if seed:
        spec["seed_company"] = seed

    keywords = _keywords(draft)
    if keywords:
        spec["keywords"] = keywords
    if draft.get("exclude_existing"):
        spec["exclude"] = {"existing_leads": True}
    return spec


def _max_results(draft: dict[str, Any], intent: str, depth: str) -> int:
    if intent == "single_company":
        return SINGLE_COMPANY_MAX_RESULTS
    asked = draft.get("max_results")
    wanted = DEFAULT_MAX_RESULTS[depth] if asked is None else int(asked)
    if not _location(draft.get("location") or {}):
        # Nowhere named: keep it small until the user says where (see `needs_confirmation`).
        wanted = min(wanted, UNLOCATED_MAX_RESULTS)
    return max(1, min(wanted, MAX_RESULTS_CEILING))


def _seed_company(raw: Any) -> dict[str, Any]:
    """The company the request is about. Without it a competitor scan compares against nothing."""
    if not isinstance(raw, dict):
        return {}
    name = str(raw.get("name") or "").strip()[:MAX_COMPANY_NAME_LENGTH]
    if not name:
        return {}
    seed: dict[str, Any] = {"name": name}
    website = str(raw.get("website") or "").strip()[:MAX_WEBSITE_LENGTH]
    if website:
        seed["website"] = website
    return seed


def _filters(draft: dict[str, Any]) -> dict[str, Any]:
    filters: dict[str, Any] = {}

    terms = _text_list(draft.get("industry_terms", []))
    if terms:
        # Free-text terms only. Mapping them onto taxonomy slugs needs the database, so the
        # planner does it (task 2.10) rather than this pure function.
        filters["industry"] = {"include": terms}

    location = _location(draft.get("location") or {})
    if location:
        filters["location"] = location

    employees = _employee_count(draft.get("employee_count") or {})
    if employees:
        filters["employee_count"] = employees

    if draft.get("has_website") is not None:
        filters["has_website"] = draft["has_website"]
    return filters


def _employee_count(raw: dict[str, Any]) -> dict[str, Any]:
    gte, lte = raw.get("gte"), raw.get("lte")
    if gte is not None and lte is not None and gte > lte:
        # "between 500 and 10 employees" matches nothing, but the job would still spend
        # credits proving it. Read it as the range the user meant.
        gte, lte = lte, gte
    return {key: value for key, value in (("gte", gte), ("lte", lte)) if value is not None}


def _location(raw: dict[str, Any]) -> dict[str, Any]:
    location: dict[str, Any] = {}
    country = str(raw.get("country") or "").strip().upper()
    if _COUNTRY.fullmatch(country):
        location["country"] = country
    for key in ("states", "cities"):
        values = _text_list(raw.get(key, []))
        if values:
            location[key] = values
    radius = raw.get("radius_km")
    if isinstance(radius, int | float) and radius > 0:
        # The contract's radius excludes zero: "within 0 km" is not a search.
        location["radius_km"] = radius
    return location


def _fields(draft: dict[str, Any], intent: str) -> list[str]:
    """Defaults for the intent first, then whatever the user named, in a stable order."""
    return _unique([*DEFAULT_FIELDS[intent], *draft.get("fields", [])])


def _keywords(draft: dict[str, Any]) -> dict[str, Any]:
    raw = draft.get("keywords") or {}
    keywords = {key: _text_list(raw.get(key, [])) for key in ("must", "should", "not")}
    return {key: value for key, value in keywords.items() if value}


def _text_list(values: Any) -> list[str]:
    """Trimmed, de-duplicated, and short enough for the contract's 100-character limit."""
    if not isinstance(values, list):
        return []
    cleaned = [str(v).strip()[:MAX_TEXT_LENGTH] for v in values if str(v).strip()]
    return _unique(cleaned)


def _unique(values: list[str]) -> list[str]:
    """Order-preserving de-duplication, case-insensitively."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        lowered = value.casefold()
        if lowered not in seen:
            seen.add(lowered)
            out.append(value)
    return out


def needs_confirmation(draft: dict[str, Any], *, min_confidence: float = 0.6) -> bool:
    """Whether to ask the user before spending credits.

    The model's own doubt counts, but is never the only thing standing between a vague request
    and the user's balance: a request that names nowhere to search is held regardless of how
    confident the model felt about it.
    """
    if draft.get("ambiguities") or draft.get("unsupported"):
        return True
    if draft.get("confidence", 0) < min_confidence:
        return True

    has_where = bool(_location(draft.get("location") or {}))
    has_what = bool(_text_list(draft.get("industry_terms", []))) or bool(
        _text_list((draft.get("keywords") or {}).get("must", []))
    )
    if draft.get("intent") in ("single_company", "competitor_scan"):
        # One named company is enough to act on, with or without a place.
        return not (_seed_company(draft.get("seed_company")) or has_where or has_what)
    # Everything else needs somewhere to look, however sure the model claims to be.
    return not has_where
