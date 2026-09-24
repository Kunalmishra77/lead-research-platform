"""Which connector can fill which field (docs/06 section 2, docs/08 capability map).

Thin on purpose. The registry already answers "who can fill this field", cheapest first, and
already knows which sources can *find* a business as opposed to answering about one somebody
named. What this adds is the planner's side of the question: what the spec asked for, what is
actually reachable, and — the part that matters to the user — what is not reachable at all, so
the job says so instead of quietly returning fewer columns than were paid for.
"""

from dataclasses import dataclass, field

from app.connectors.base import BaseConnector
from app.connectors.registry import ConnectorRegistry


@dataclass(frozen=True, slots=True)
class FieldPlan:
    """What can fill one requested field."""

    field: str
    #: Sources that could find businesses *by* this field, cheapest first.
    discovery: tuple[str, ...]
    #: Sources that could fill it once a business is known. A superset of `discovery`.
    enrichment: tuple[str, ...]

    @property
    def satisfiable(self) -> bool:
        return bool(self.enrichment)


@dataclass(frozen=True, slots=True)
class Capabilities:
    """The capability map for one spec."""

    by_field: dict[str, FieldPlan]
    #: What one call of each source costs internally, in micros. The planner needs it to fund a
    #: task enough to make at least one: a budget below that buys nothing at all.
    cost_by_source: dict[str, int] = field(default_factory=dict)

    @property
    def unsatisfiable(self) -> tuple[str, ...]:
        """Requested fields no enabled connector can fill.

        Surfaced rather than swallowed: docs/06 section 9 ends a job `unsatisfiable` for these,
        and the user is owed the difference between "we looked and there is none" and "nothing
        we have could ever have answered that".
        """
        return tuple(f for f, plan in self.by_field.items() if not plan.satisfiable)

    def discovery_sources(self) -> tuple[str, ...]:
        """Every source that can find businesses for this spec, cheapest first, deduplicated."""
        seen: dict[str, None] = {}
        for plan in self.by_field.values():
            for key in plan.discovery:
                seen.setdefault(key, None)
        return tuple(seen)


def build_capabilities(
    registry: ConnectorRegistry, fields: list[str], *, legal_approved: bool = False
) -> Capabilities:
    """Map the spec's requested fields onto the connectors registered for this job.

    `registry.providing` is asked twice per field, and the difference is the whole point: a
    source that only answers about a business someone already named is useful for filling a
    column and useless for finding rows. Being cheaper, it would otherwise sort to the front of
    a discovery plan and the job would find nothing (`discovers`, docs/08).
    """
    return Capabilities(
        cost_by_source={
            c.key: c.cost_per_call_micros
            for c in registry
            if c.usable_in_production(legal_approved=legal_approved)
        },
        by_field={
            field: FieldPlan(
                field=field,
                discovery=_keys(
                    registry.providing(field, legal_approved=legal_approved, for_discovery=True)
                ),
                enrichment=_keys(registry.providing(field, legal_approved=legal_approved)),
            )
            for field in fields
        },
    )


def _keys(connectors: list[BaseConnector]) -> tuple[str, ...]:
    return tuple(c.key for c in connectors)
