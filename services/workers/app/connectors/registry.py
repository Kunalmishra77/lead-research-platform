"""Connector registry: the planner asks "who can fill this field?" (docs/08 capability map).

Holds one shared instance per source, so a registered connector must be stateless.
"""

from collections.abc import Iterable, Iterator

from app.connectors.base import BaseConnector


class ConnectorRegistry:
    def __init__(self) -> None:
        self._by_key: dict[str, BaseConnector] = {}

    def register(self, connector: BaseConnector) -> BaseConnector:
        key = connector.key
        if key in self._by_key:
            raise ValueError(f"connector {key} is already registered")
        self._by_key[key] = connector
        return connector

    def get(self, key: str) -> BaseConnector:
        """Lookup by key. Does not apply source policy; see `providing`/`enabled`."""
        try:
            return self._by_key[key]
        except KeyError:
            raise KeyError(f"unknown connector: {key}") from None

    def __contains__(self, key: object) -> bool:
        return key in self._by_key

    def __iter__(self) -> Iterator[BaseConnector]:
        return iter(self._by_key.values())

    def __len__(self) -> int:
        return len(self._by_key)

    @property
    def keys(self) -> list[str]:
        return sorted(self._by_key)

    def providing(self, field: str, *, legal_approved: bool = False) -> list[BaseConnector]:
        """Connectors that may fill `field`, cheapest call first (yield stats arrive in 2.10).

        Red sources are left out unless legal review has approved them (docs/08 source policy),
        so the planner cannot reach for one by accident.
        """
        return sorted(
            (
                c
                for c in self._by_key.values()
                if field in c.fields_provided
                and c.usable_in_production(legal_approved=legal_approved)
            ),
            key=lambda c: (c.cost_per_call_micros, c.key),
        )

    def enabled(self, keys: Iterable[str], *, legal_approved: bool = False) -> list[BaseConnector]:
        """The usable registered connectors among `keys` (e.g. sources enabled for this org).

        An org enabling a red source is not enough on its own: legal review decides (docs/08).
        """
        return [
            c
            for k in keys
            if (c := self._by_key.get(k)) is not None
            and c.usable_in_production(legal_approved=legal_approved)
        ]
