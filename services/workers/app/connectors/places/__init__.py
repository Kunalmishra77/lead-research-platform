"""Discovery from the local place index (ADR-0016, ADR-0017).

`overture` finds the leads; `osm` is the second dataset Phase 4 compares them against. Both read
a SQLite file on disk, so neither makes a network call, costs anything per row, or can be
rate-limited or switched off by anyone.
"""

from app.connectors.places.connector import (
    FIELDS,
    OsmConnector,
    OvertureConnector,
    PlaceIndexConnector,
)
from app.connectors.places.mapping import to_candidate, to_field_values

__all__ = [
    "FIELDS",
    "OsmConnector",
    "OvertureConnector",
    "PlaceIndexConnector",
    "to_candidate",
    "to_field_values",
]
