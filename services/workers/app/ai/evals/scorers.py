"""How an eval case is scored (docs/07 evals).

Each scorer answers two things: did this case pass, and where exactly did it differ. The second
matters more in practice — a score that drops from 0.94 to 0.88 is only actionable if you can see
which fields moved.
"""

from dataclasses import dataclass, field
from typing import Any

from app.ai.spec_draft import build_spec, needs_confirmation

#: Fields of a parsed spec that are checked case by case. Everything else is either derived
#: deterministically or too incidental to fail a case over.
SPEC_CHECKS: tuple[str, ...] = (
    "intent",
    "seed_company",
    "industry",
    "country",
    "cities",
    "states",
    "radius_km",
    "employee_count",
    "has_website",
    "fields",
    "max_results",
    "depth",
    "keywords_must",
    "keywords_not",
    "exclude_existing",
    "unsupported",
    # The gate that stops a vague request from spending the user's credits. Scored like any
    # other field, because getting it wrong costs real money either way.
    "needs_confirmation",
)

#: A case may allow extra words in a free-text term by setting this to true, for requests where
#: more than one honest phrasing exists ("NEET" and "NEET preparation" are both faithful).
SUPERSET_FLAG = "allow_extra_words"


@dataclass
class CaseScore:
    case_id: str
    passed: bool
    #: field -> (expected, actual) for everything that did not match.
    diffs: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    #: Per-field score, so a near miss is visible as such rather than just "failed".
    checked: int = 0
    matched: int = 0
    note: str = ""

    @property
    def field_accuracy(self) -> float:
        return self.matched / self.checked if self.checked else 0.0


def score_intent_classify(expected: dict[str, Any], actual: dict[str, Any]) -> CaseScore:
    """Exact match on the intent. Confidence is reported, never scored."""
    want = expected["intent"]
    got = actual.get("intent")
    passed = want == got
    return CaseScore(
        case_id="",
        passed=passed,
        diffs={} if passed else {"intent": (want, got)},
        checked=1,
        matched=int(passed),
        note=f"confidence={actual.get('confidence')}",
    )


def score_spec_parse(expected: dict[str, Any], actual: dict[str, Any]) -> CaseScore:
    """Compares the spec the pipeline would actually build, not the raw draft.

    A case only passes when every checked field matches, because a spec with the right intent and
    the wrong city sends the whole job to the wrong place.
    """
    spec = build_spec(actual)
    want = _expected_view(expected)
    got = _spec_view(spec, actual)
    allow_extra = bool(expected.get(SUPERSET_FLAG))

    diffs: dict[str, tuple[Any, Any]] = {}
    checked = 0
    matched = 0
    for key in SPEC_CHECKS:
        if key not in want:
            continue  # the case does not pin this field
        checked += 1
        free_text = allow_extra and key in FREE_TEXT_FIELDS
        if _equal(want[key], got.get(key), free_text=free_text):
            matched += 1
        else:
            diffs[key] = (want[key], got.get(key))
    if not checked:
        raise ValueError("an eval case that pins nothing would pass vacuously")

    return CaseScore(
        case_id="",
        passed=not diffs,
        diffs=diffs,
        checked=checked,
        matched=matched,
        note=f"confidence={actual.get('confidence')}",
    )


def _expected_view(expected: dict[str, Any]) -> dict[str, Any]:
    """A case pins only the fields it cares about; the rest are not checked."""
    return {key: value for key, value in expected.items() if key in SPEC_CHECKS}


def _spec_view(spec: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    filters = spec.get("filters", {})
    location = filters.get("location", {})
    keywords = spec.get("keywords", {})
    return {
        "intent": spec["intent"],
        "seed_company": spec.get("seed_company", {}).get("name"),
        "industry": filters.get("industry", {}).get("include", []),
        "country": location.get("country"),
        "cities": location.get("cities", []),
        "states": location.get("states", []),
        "radius_km": location.get("radius_km"),
        "employee_count": filters.get("employee_count", {}),
        "has_website": filters.get("has_website"),
        "fields": spec["fields"],
        "max_results": spec["limits"]["max_results"],
        "depth": spec["depth"],
        "keywords_must": keywords.get("must", []),
        "keywords_not": keywords.get("not", []),
        "exclude_existing": bool(spec.get("exclude", {}).get("existing_leads", False)),
        "unsupported": draft.get("unsupported", []),
        "needs_confirmation": needs_confirmation(draft),
    }


#: Fields whose values are the user's own words, where phrasing must not decide a pass.
FREE_TEXT_FIELDS: frozenset[str] = frozenset(
    {"industry", "keywords_must", "keywords_not", "unsupported"}
)


def _equal(want: Any, got: Any, *, free_text: bool = False) -> bool:
    """Order and case never matter for list fields; a name is a name."""
    if isinstance(want, list) and isinstance(got, list):
        if free_text:
            return _terms_preserved(want, got)
        return sorted(str(v).casefold() for v in want) == sorted(str(v).casefold() for v in got)
    if isinstance(want, str) and isinstance(got, str):
        return want.casefold() == got.casefold()
    return bool(want == got)


def _terms_preserved(want: list[Any], got: list[Any]) -> bool:
    """Each expected term must survive in some actual term, word for word.

    Only for cases that opt in: adding a qualifier narrows a search exactly as much as dropping
    one widens it, so by default the words must match. Where more than one honest reading exists
    — "NEET" and "NEET preparation" — the case says so and this rule applies.
    """
    if len(want) != len(got):
        return False
    remaining = [_words(str(v)) for v in got]
    for value in want:
        expected = _words(str(value))
        match = next((i for i, actual in enumerate(remaining) if expected <= actual), None)
        if match is None:
            return False
        remaining.pop(match)
    return True


def _words(value: str) -> frozenset[str]:
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in value.casefold())
    return frozenset(part for part in cleaned.split() if part)


SCORERS = {
    "intent_classify": score_intent_classify,
    "spec_parse": score_spec_parse,
}
