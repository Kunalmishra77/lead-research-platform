"""Which filters a search can actually act on (docs/08 capability map, docs/09 feasibility badges).

The user is shown this before they spend anything, so it has to be honest about the difference
between three very different things:

* `directly_searchable` — the source can be asked for it, so it costs nothing extra.
* `post_filter` — we have to fetch candidates and then throw some away. It works, but the user
  pays for results they never see, so the UI says so.
* `estimate_only` — we cannot verify it, only guess. Phase 2 has none of these; enrichment adds
  them.
* `unsupported` — nothing in the system applies it. Saying this out loud is the point: a filter
  we silently drop is one the user believes is running.

The table is per phase. Phase 2 discovers through Google Places and a SERP vendor, so a business
type and a place are searchable and almost nothing else is.
"""

from typing import Any, Final, Literal, TypedDict

Mode = Literal["directly_searchable", "post_filter", "estimate_only", "unsupported"]


class FeasibilityRow(TypedDict):
    """One line of the feasibility table, shaped like the reply contract."""

    filter: str
    mode: Mode
    note: str


#: filter path -> (mode, note). The note is shown to the user, so it explains the cost.
#:
#: Every filter the schema allows has a row here, and `test_feasibility.py` reads
#: `research-spec.schema.json` and fails if one is missing. That test exists because the table
#: is a promise and nothing used to check it: `filters.has_website` sat here as a `post_filter`
#: saying "results without one are discarded" while nothing in the pipeline read the filter at
#: all, and seven other filters had no row whatsoever -- neither applied nor declared
#: unsupported, so a user asking for them was told nothing and given everything.
FILTER_MODES: Final[dict[str, tuple[Mode, str]]] = {
    "filters.industry": ("directly_searchable", "Searched as a category on the map sources."),
    "filters.location": (
        "directly_searchable",
        "Searched by covering the cities and states you named, from the geography seed.",
    ),
    # Sub-fields of a filter that is itself applied. Calling `filters.location`
    # directly_searchable is a promise about all of it, and these two are not kept: the geo seed
    # holds cities and states with their own boxes, so there is nothing to grow a radius from or
    # to expand a metro area with.
    "filters.location.radius_km": (
        "unsupported",
        "A radius needs a point to grow from; the geography seed holds named areas and their "
        "own bounding boxes.",
    ),
    "filters.location.include_metro_area": (
        "unsupported",
        "Metro areas are not in the geography seed, so a city is searched as the seed defines "
        "it.",
    ),
    "filters.has_website": (
        "post_filter",
        "Asked of the place index directly; for sources that cannot filter on it, results "
        "without a website are discarded after being found.",
    ),
    "filters.contact": (
        "post_filter",
        "Phone and email are checked on every result. WhatsApp and contact forms cannot be "
        "checked during discovery, so asking only for those filters nothing.",
    ),
    "filters.min_confidence": (
        "unsupported",
        "Per-field confidence is computed in the verification stage, which runs after "
        "discovery.",
    ),
    "filters.employee_count": (
        "unsupported",
        "Company size is not available from the Phase 2 sources.",
    ),
    "filters.rating": (
        "unsupported",
        "Ratings come from Google Places, which is not the discovery source; the bulk place "
        "releases do not carry them.",
    ),
    "filters.social": (
        "post_filter",
        "Whether a profile exists is checked on each result and the rest are discarded, so you "
        "pay for results you do not see.",
    ),
    "filters.social.active_within_days": (
        "unsupported",
        "Whether a profile is active needs the enrichment stage; only its existence is checked "
        "during discovery.",
    ),
    "filters.technologies": (
        "unsupported",
        "What a site is built with is read by the enrichment stage, after discovery.",
    ),
    "filters.funding": (
        "unsupported",
        "Funding history needs a provider none of the discovery sources is.",
    ),
    "filters.hiring": (
        "unsupported",
        "Open roles are read by the enrichment stage, after discovery.",
    ),
    # These read "post_filter" for months and nothing matched a keyword against anything.
    # Discovery sees a name and a category; the text a keyword is really about is on the
    # business's own site, which the enrichment stage reads. Half-applying it would be worse
    # than saying so: "vegan restaurants" matched against names alone misses most of them and
    # looks like a complete answer.
    "keywords.must": (
        "unsupported",
        "Keywords are matched against the text collected from each site, which the enrichment "
        "stage reads after discovery.",
    ),
    "keywords.not": (
        "unsupported",
        "Excluding by keyword needs the site text that the enrichment stage collects.",
    ),
    "keywords.should": (
        "unsupported",
        "Ranking by keyword needs the site text that the enrichment stage collects.",
    ),
    "exclude.existing_leads": (
        "post_filter",
        "Checked against your workspace as each result is delivered, so you are not charged "
        "again for one you already have.",
    ),
    "exclude.lists": (
        "unsupported",
        "Excluding by list needs the lists feature, which arrives with the lead database.",
    ),
    "custom_columns": (
        "unsupported",
        "Columns answered by AI research arrive in a later stage.",
    ),
    "seed_company": (
        "unsupported",
        "Finding businesses similar to a named one needs the company graph, which arrives "
        "after discovery.",
    ),
}

#: filter path -> where the pipeline applies it. A filter whose mode says it runs must appear
#: here, and one declared `unsupported` must not: `test_feasibility.py` asserts both, so the
#: table cannot promise work that no code does.
#:
#: The value is read by a human, not by code. Its job is to make the claim checkable -- someone
#: doubting a row can open the named place and see the filter, which is the whole reason
#: `filters.has_website` stayed false for as long as it did.
APPLIED_BY: Final[dict[str, str]] = {
    "filters.industry": "planner/plan.py: _categories() puts them on the discovery task",
    "filters.location": "planner/plan.py: _resolve_places() then one _search() per area",
    "filters.has_website": (
        "places/index.py: a predicate in search() and count();"
        " handlers/discovery.py: _honour_website_filter() before anything is stored"
    ),
    "filters.contact": (
        "places/index.py: _contact_where() in search() and count();"
        " handlers/discovery.py: _honour_contact_filter() before anything is stored"
    ),
    "filters.social": "handlers/discovery.py: _honour_social_filter() before anything is stored",
    "exclude.existing_leads": "db/batch.py: deliver() -- a lead the workspace holds is not new",
}

#: A spec asking for these fields is asking for work the discovery phase cannot do yet.
UNSUPPORTED_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "email",
        "whatsapp",
        "contact_form",
        "employee_band",
        "founded_year",
        "technologies",
        "hiring",
        "people",
        "instagram",
        "facebook",
        "linkedin",
        "x",
        "youtube",
    }
)


#: The order filter rows appear in, which is the order `research-spec.schema.json` declares
#: them: a user reading the table should find it in the same shape as the thing they asked for.
_FILTER_ORDER: Final[tuple[str, ...]] = (
    "industry",
    "location",
    "has_website",
    "contact",
    "min_confidence",
    "employee_count",
    "rating",
    "social",
    "technologies",
    "funding",
    "hiring",
)


def assess(
    spec: dict[str, Any], unsupported_notes: list[str] | None = None
) -> list[FeasibilityRow]:
    """One row per thing the user asked for, in the order the spec lists them."""
    rows: list[FeasibilityRow] = []
    filters = spec.get("filters", {})

    # Every filter present, in the schema's order -- not a hand-written list of four. The old
    # list named industry, location, has_website and employee_count, so a spec asking about
    # ratings, socials, technologies, funding, hiring, contact channels or confidence produced
    # no row at all: the user was told nothing and given everything.
    for key in _FILTER_ORDER:
        if filters.get(key) not in (None, {}, [], ()):
            rows.append(_row(f"filters.{key}"))
    if spec.get("seed_company"):
        rows.append(_row("seed_company"))
    if spec.get("custom_columns"):
        rows.append(_row("custom_columns"))
    for key in ("must", "not", "should"):
        if spec.get("keywords", {}).get(key):
            rows.append(_row(f"keywords.{key}"))
    exclude = spec.get("exclude", {})
    if exclude.get("existing_leads"):
        rows.append(_row("exclude.existing_leads"))
    if exclude.get("lists"):
        rows.append(_row("exclude.lists"))

    wanted = UNSUPPORTED_FIELDS.intersection(spec.get("fields", []))
    if wanted:
        rows.append(
            {
                "filter": "fields",
                "mode": "unsupported",
                "note": (
                    f"Not collected during discovery: {', '.join(sorted(wanted))}. "
                    "These arrive in a later stage."
                ),
            }
        )

    for note in unsupported_notes or []:
        rows.append({"filter": "request", "mode": "unsupported", "note": note[:200]})
    return rows


def _row(key: str) -> FeasibilityRow:
    mode, note = FILTER_MODES[key]
    return {"filter": key, "mode": mode, "note": note}
