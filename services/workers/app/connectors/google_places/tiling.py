"""Covering an area with searches (task 2.8 geography tiling).

Text Search returns at most 60 results for a query, however big the area (verified in the API
docs). A city with 900 dental clinics therefore needs the city split into pieces small enough
that each piece has fewer than 60 — otherwise the other 840 are simply never seen, and nothing
in the response says so.

Tiles are plain latitude/longitude rectangles. That makes them slightly uneven in ground area
away from the equator, which costs a little efficiency and no correctness: a tile that is too
small only means a cheap search that returns everything it contains.
"""

import math
from dataclasses import dataclass
from typing import Final

#: The API's hard ceiling on results per query, across all pages.
MAX_RESULTS_PER_QUERY: Final = 60

#: Roughly 111 km per degree of latitude; used to keep tiles a sane size on the ground.
KM_PER_DEGREE_LAT: Final = 111.0

#: Below this a tile is smaller than a city block and splitting further wastes calls.
MIN_TILE_SPAN_DEG: Final = 0.01


@dataclass(frozen=True, slots=True)
class Tile:
    """A latitude/longitude rectangle to search inside."""

    min_lat: float
    min_lng: float
    max_lat: float
    max_lng: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.min_lat + self.max_lat) / 2, (self.min_lng + self.max_lng) / 2)

    @property
    def span_lat(self) -> float:
        return self.max_lat - self.min_lat

    @property
    def span_lng(self) -> float:
        return self.max_lng - self.min_lng

    def width_km(self) -> float:
        """Ground width at this tile's latitude, where longitude degrees are shorter."""
        return self.span_lng * KM_PER_DEGREE_LAT * math.cos(math.radians(self.center[0]))

    def height_km(self) -> float:
        return self.span_lat * KM_PER_DEGREE_LAT

    def as_rectangle(self) -> dict[str, dict[str, float]]:
        """The `locationRestriction.rectangle` shape the API expects."""
        return {
            "low": {"latitude": self.min_lat, "longitude": self.min_lng},
            "high": {"latitude": self.max_lat, "longitude": self.max_lng},
        }

    def quarters(self) -> list["Tile"]:
        """Splits into four. Used when a search came back full and may have been truncated."""
        mid_lat, mid_lng = self.center
        if self.span_lat <= MIN_TILE_SPAN_DEG or self.span_lng <= MIN_TILE_SPAN_DEG:
            return []
        return [
            Tile(self.min_lat, self.min_lng, mid_lat, mid_lng),
            Tile(self.min_lat, mid_lng, mid_lat, self.max_lng),
            Tile(mid_lat, self.min_lng, self.max_lat, mid_lng),
            Tile(mid_lat, mid_lng, self.max_lat, self.max_lng),
        ]


def tile_bbox(bbox: tuple[float, float, float, float], max_km: float = 15.0) -> list[Tile]:
    """Splits a bounding box into tiles no wider or taller than `max_km`.

    A first pass over a whole city would hit the 60-result ceiling immediately, so the area is
    divided up front rather than only when a search comes back full.
    """
    min_lat, min_lng, max_lat, max_lng = bbox
    if max_lat <= min_lat or max_lng <= min_lng:
        return []

    whole = Tile(min_lat, min_lng, max_lat, max_lng)
    rows = max(1, math.ceil(whole.height_km() / max_km))
    columns = max(1, math.ceil(whole.width_km() / max_km))

    lat_step = (max_lat - min_lat) / rows
    lng_step = (max_lng - min_lng) / columns
    return [
        Tile(
            min_lat + row * lat_step,
            min_lng + column * lng_step,
            min_lat + (row + 1) * lat_step,
            min_lng + (column + 1) * lng_step,
        )
        for row in range(rows)
        for column in range(columns)
    ]


#: How far a tile may be split before the coverage we have is accepted: 1 + 4 + 16 searches.
MAX_SPLIT_DEPTH: Final = 2
