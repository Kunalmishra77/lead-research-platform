"""What each kind of request is worth spending, per intent (docs/06 section 2).

docs/06 calls these "deterministic plan templates per intent" and puts them in YAML. They are
Python here, because every value in them is a number the planner arithmetic reads and a YAML file
would buy nothing but a parser and a class of typo that only shows up at runtime. If they ever
need editing without a deploy they move to the database, not to a file beside the code.

`ResearchSpec.intent` is the one that keys these — why the user wants the leads — not the
`intent_classify` enum, which answers where to look. Confusing the two sends a job to the wrong
sources (docs/07).
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class PlanTemplate:
    """The shape of a plan for one intent."""

    intent: str
    #: How much of the job's **reserved** credits discovery may have. The rest is left for the
    #: stages after it — enrichment and verification in Phase 3 — which cannot bid for it
    #: retrospectively because `research_tasks.credit_budget` is write-once (the worker has no
    #: UPDATE grant). So this must stay below 1.0: whatever it does not take is gone.
    discovery_share: float
    #: Sub-localities are worth searching separately when covering a market matters more than
    #: finding a few examples of it.
    tile_sub_localities: bool
    #: Cap on discovery tasks, so a spec naming 50 cities cannot plan 800 searches.
    max_discovery_tasks: int
    note: str


#: Depth used to multiply the discovery share, and that was backwards. The job's total is fixed
#: by what the API reserved, so depth cannot change how much is spent — only how it is divided,
#: and "deep" means more per row, which is *less* for finding rows and more for the stages after.
#: As a multiplier it also let `market_map` + `deep` reach 1.125, clamped to 1.0, handing
#: discovery the entire budget and leaving Phase 3 enrichment permanently unfunded. Depth belongs
#: on per-row thoroughness (Phase 3), so nothing here reads it.

TEMPLATES: Final[dict[str, PlanTemplate]] = {
    "prospecting": PlanTemplate(
        intent="prospecting",
        discovery_share=0.6,
        tile_sub_localities=True,
        max_discovery_tasks=60,
        note="Finding as many real businesses as the budget allows; coverage is the point.",
    ),
    "market_map": PlanTemplate(
        intent="market_map",
        # A market map is judged on completeness, so more of the budget goes to finding rows and
        # less to enriching each one.
        discovery_share=0.75,
        tile_sub_localities=True,
        max_discovery_tasks=90,
        note="Completeness matters more than depth per row.",
    ),
    "competitor_scan": PlanTemplate(
        intent="competitor_scan",
        discovery_share=0.5,
        tile_sub_localities=False,
        max_discovery_tasks=20,
        note="A handful of comparable businesses, each one worth knowing well.",
    ),
    "hiring_signal": PlanTemplate(
        intent="hiring_signal",
        # The signal is not on a map; discovery only assembles the list to check (Phase 5).
        discovery_share=0.4,
        tile_sub_localities=False,
        max_discovery_tasks=30,
        note="Discovery is the cheap half; the signal comes from crawling later.",
    ),
    "single_company": PlanTemplate(
        intent="single_company",
        discovery_share=0.3,
        tile_sub_localities=False,
        max_discovery_tasks=2,
        note="One business. Discovery is only finding it; everything else is depth.",
    ),
}


def template_for(intent: str) -> PlanTemplate:
    """The template for an intent, falling back to prospecting.

    A fallback rather than an error on purpose: `ResearchSpec.intent` is a closed enum in the
    contract, so an unknown value here means the contract grew and this table did not. Failing
    the job would punish the user for our omission, and prospecting is the most conservative
    shape — the smallest discovery share of the two that tile.
    """
    return TEMPLATES.get(intent, TEMPLATES["prospecting"])
