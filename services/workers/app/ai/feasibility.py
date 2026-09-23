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
FILTER_MODES: Final[dict[str, tuple[Mode, str]]] = {
    "filters.industry": ("directly_searchable", "Searched as a category on the map sources."),
    "filters.location": ("directly_searchable", "Searched by covering the area you named."),
    "filters.has_website": (
        "post_filter",
        "Checked after each business is found, so results without one are discarded.",
    ),
    "filters.employee_count": (
        "unsupported",
        "Company size is not available from the Phase 2 sources.",
    ),
    "keywords.must": ("post_filter", "Matched against the text we collect for each business."),
    "keywords.not": ("post_filter", "Matched against the text we collect, to exclude."),
    "keywords.should": ("post_filter", "Used to rank rather than to exclude."),
    "exclude.existing_leads": (
        "post_filter",
        "Checked against your workspace as each result is delivered, so you are not charged "
        "again for one you already have.",
    ),
    "seed_company": (
        "unsupported",
        "Finding businesses similar to a named one needs the company graph, which arrives "
        "after discovery.",
    ),
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


def assess(
    spec: dict[str, Any], unsupported_notes: list[str] | None = None
) -> list[FeasibilityRow]:
    """One row per thing the user asked for, in the order the spec lists them."""
    rows: list[FeasibilityRow] = []
    filters = spec.get("filters", {})

    for key in ("industry", "location", "has_website", "employee_count"):
        if key in filters:
            rows.append(_row(f"filters.{key}"))
    if spec.get("seed_company"):
        rows.append(_row("seed_company"))
    for key in ("must", "not", "should"):
        if spec.get("keywords", {}).get(key):
            rows.append(_row(f"keywords.{key}"))
    if spec.get("exclude", {}).get("existing_leads"):
        rows.append(_row("exclude.existing_leads"))

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
