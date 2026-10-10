"""Bulk place datasets as the discovery source (ADR-0016, ADR-0017).

Discovery reads a file, not an API. A dataset is extracted once into a SQLite place index on
local disk, which has no request quota, no third party able to throttle it, and no per-row cost
in the database we are actually short of.

    index.py      the index: build it, query it by bounding box and category
    model.py      one business, and the rules deciding something is one
    overture.py   Overture Maps Places -> index. The discovery source.
    osm.py        OpenStreetMap -> index. The second opinion Phase 4 verifies against.
    geofabrik.py  downloading an OSM extract

Measured on the same Delhi box, which is why Overture leads:

    OSM        21,879 businesses    9.7% phone    2.7% email
    Overture  283,302 businesses   67.1% own phone   38.0% own email

Licences travel with the data. Overture is CDLA-Permissive-2.0 (Apache 2.0 for Foursquare-derived
rows, CC0 for AllThePlaces) and an export built from it must carry the agreement text. OSM is
ODbL 1.0 and requires attribution wherever its values are shown.
"""

from app.places.geofabrik import Extract, fetch_extract, region_url
from app.places.index import IndexStats, IndexWriter, PlaceIndex
from app.places.model import (
    OSM,
    OVERTURE,
    Place,
    is_business,
    looks_like_email,
    looks_like_phone,
    looks_like_url,
    osm_place,
)
from app.places.osm import IngestReport
from app.places.osm import build_index as build_osm_index
from app.places.overture import OvertureReport
from app.places.overture import build_index as build_overture_index

__all__ = [
    "OSM",
    "OVERTURE",
    "Extract",
    "IndexStats",
    "IndexWriter",
    "IngestReport",
    "OvertureReport",
    "Place",
    "PlaceIndex",
    "build_osm_index",
    "build_overture_index",
    "fetch_extract",
    "is_business",
    "looks_like_email",
    "looks_like_phone",
    "looks_like_url",
    "osm_place",
    "region_url",
]
