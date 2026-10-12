"""The feasibility table is a promise. These tests are what checks it.

Its own docstring says the point out loud: "a filter we silently drop is one the user believes
is running." It was dropping seven of them. `filters.has_website` sat in the table as a
`post_filter` claiming "results without one are discarded" while nothing in the pipeline read
the filter, and `rating`, `social`, `technologies`, `funding`, `hiring`, `min_confidence` and
`contact` had no row at all -- so a user asking for any of them was told nothing and given
everything.

A table nobody checks drifts from the code the moment either changes. So two things are checked
mechanically here: every filter the schema allows has a row, and every row that claims work
gets done names where the code does it.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.ai.feasibility import APPLIED_BY, FILTER_MODES, assess

#: Modes that tell the user the filter changes what they get.
RUNS = ("directly_searchable", "post_filter")


def _schema() -> dict[str, Any]:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "packages" / "contracts" / "schemas" / "research-spec.schema.json"
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise AssertionError(f"research-spec.schema.json not found above {here}")


def test_every_filter_the_schema_allows_has_a_mode() -> None:
    """A filter with no row is the worst case: not applied, and not declared unapplied.

    This is the test that makes the next one impossible to add silently. Someone extending
    `Filters` has to say here whether it runs -- which is a thirty-second decision, and the
    alternative is a user paying for a search that quietly ignored half of what they asked.
    """
    allowed = set(_schema()["$defs"]["Filters"]["properties"])
    missing = sorted(key for key in allowed if f"filters.{key}" not in FILTER_MODES)
    assert missing == [], f"filters with no declared mode: {missing}"


def test_a_sub_field_of_an_applied_filter_is_declared_too() -> None:
    """Calling `filters.location` directly_searchable is a promise about all of it.

    It holds for cities, states and country and not for `radius_km` or `include_metro_area` --
    the geography seed stores named areas with their own boxes, so there is no point to grow a
    radius from. A sub-field nobody declares is the same silence as a filter nobody declares,
    one level down.
    """
    location = set(_schema()["$defs"]["Location"]["properties"])
    searched = {"cities", "states", "country"}
    for key in sorted(location - searched):
        assert f"filters.location.{key}" in FILTER_MODES, (
            f"filters.location.{key} is neither searched nor declared unsupported"
        )


def test_every_top_level_ask_has_a_mode() -> None:
    """The same for the asks that are not under `filters`."""
    for path in ("seed_company", "custom_columns", "exclude.existing_leads", "exclude.lists"):
        assert path in FILTER_MODES, f"{path} has no declared mode"
    for key in ("must", "not", "should"):
        assert f"keywords.{key}" in FILTER_MODES


def test_a_filter_that_claims_to_run_names_where_it_runs() -> None:
    """`post_filter` and `directly_searchable` are claims about code. This is the receipt."""
    claimed = sorted(path for path, (mode, _) in FILTER_MODES.items() if mode in RUNS)
    unbacked = [path for path in claimed if not APPLIED_BY.get(path)]
    assert unbacked == [], f"these promise to run but name no code: {unbacked}"


def test_the_code_a_filter_names_exists() -> None:
    """Checked, not trusted. `APPLIED_BY` is prose, and prose can name a function nobody wrote.

    It nearly did: `filters.contact` was given its row and its applier name in the same edit
    that discovered the filter was never read, and for a few minutes the table claimed a
    `_honour_contact_filter` that did not exist -- the exact shape of the bug the table was
    being fixed for. So every `module.py: ... name` reference is resolved here.
    """
    root = Path(__file__).resolve().parents[1] / "app"
    for path, where in sorted(APPLIED_BY.items()):
        for claim in where.split(";"):
            module, _, detail = claim.partition(":")
            module_path = root / module.strip()
            assert module_path.is_file(), f"{path} names {module.strip()}, which is not a file"
            source = module_path.read_text(encoding="utf-8")
            for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?=\(\))", detail):
                assert f"def {name}(" in source, (
                    f"{path} names {name}() in {module.strip()}, which has no such function"
                )
            named = re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?=\(\))", detail)
            assert named, (
                f"{path} points at {module.strip()} but names no function. Prose alone cannot "
                "be checked, which is how `keywords.must` claimed to be applied in "
                "crawl/discovery.py for months while nothing there matched a keyword."
            )


def test_a_filter_declared_unsupported_does_not_claim_an_applier() -> None:
    """The other direction, so the two tables cannot drift apart in silence.

    A filter that has grown an applier but still reads `unsupported` is a different lie: the
    user is told it will be ignored, declines to rely on it, and pays for a narrower search than
    they were promised.
    """
    for path, (mode, _) in FILTER_MODES.items():
        if mode == "unsupported":
            assert path not in APPLIED_BY, f"{path} says unsupported but names an applier"


def test_every_note_explains_the_cost_rather_than_restating_the_mode() -> None:
    """The note is the only part the user reads. An empty or three-word one is a row that
    tells them a filter is a `post_filter` and nothing about what that costs them."""
    for path, (_, note) in FILTER_MODES.items():
        assert len(note.split()) >= 6, f"{path} has a note too short to be useful: {note!r}"
        assert note.endswith("."), f"{path}: {note!r}"


# ---------------------------------------------------------------- what assess() reports


def _spec(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "spec_version": 1,
        "entity": "company",
        "intent": "prospecting",
        "filters": {"location": {"country": "IN", "cities": ["Delhi"]}},
        "fields": ["name"],
        "depth": "standard",
        "limits": {"max_results": 250, "max_credits": 500},
    }
    base.update(extra)
    return base


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("rating", {"gte": 4.0}),
        ("social", {"required": True}),
        ("technologies", {"include": ["shopify"]}),
        ("funding", {"last_round_within_days": 365}),
        ("hiring", {"required": True}),
        ("min_confidence", 0.8),
        ("employee_count", {"gte": 10}),
        ("contact", {"any_of": ["phone"]}),
        ("has_website", True),
    ],
)
def test_a_filter_the_user_asked_for_gets_a_row(key: str, value: Any) -> None:
    """Every one of these produced no row before. The user asked and was told nothing."""
    spec = _spec()
    spec["filters"][key] = value
    rows = assess(spec)
    assert any(r["filter"] == f"filters.{key}" for r in rows), (
        f"filters.{key} produced no feasibility row: {[r['filter'] for r in rows]}"
    )


def test_an_empty_filter_object_is_not_an_ask() -> None:
    """A parser that writes `{}` for something the user never mentioned must not make the
    table claim they asked about it."""
    spec = _spec()
    spec["filters"]["rating"] = {}
    assert not any(r["filter"] == "filters.rating" for r in assess(spec))


def test_rows_come_out_in_the_order_the_spec_declares_them() -> None:
    spec = _spec()
    spec["filters"].update({"rating": {"gte": 4.0}, "has_website": True})
    spec["filters"]["industry"] = {"include": ["restaurant"]}
    order = [r["filter"] for r in assess(spec) if r["filter"].startswith("filters.")]
    assert order == [
        "filters.industry",
        "filters.location",
        "filters.has_website",
        "filters.rating",
    ]
