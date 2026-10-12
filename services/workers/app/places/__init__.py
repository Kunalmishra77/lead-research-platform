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

Only the read side is re-exported here, and that is load-bearing. Importing anything from this
package runs this file, so a convenience re-export of the ingest modules made every worker import
`osmium` and `duckdb` -- and `osmium` needs `libexpat`, which the slim runtime image does not
carry. Every worker crash-looped on `ImportError: libexpat.so.1` while the ingest they were
loading it for runs once a month from an operator's shell.

The build modules are imported directly by the thing that builds (`__main__`), where the cost
belongs:

    from app.places.osm import build_index        # needs osmium
    from app.places.overture import build_index   # needs duckdb
"""

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

__all__ = [
    "OSM",
    "OVERTURE",
    "IndexStats",
    "IndexWriter",
    "Place",
    "PlaceIndex",
    "is_business",
    "looks_like_email",
    "looks_like_phone",
    "looks_like_url",
    "osm_place",
]
