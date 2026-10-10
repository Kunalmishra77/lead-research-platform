"""OpenStreetMap as a bulk discovery source (ADR-0016).

Discovery reads a file, not an API. An extract is downloaded once and reduced to a SQLite place
index on local disk, which has no request quota, no third party to be throttled by, and no cost
per row -- the three things that made every API-shaped option unable to reach the volume this
product needs.

    download.py  fetch an extract from Geofabrik, checksum-verified
    ingest.py    stream it into an index
    index.py     the index: build it, query it by bounding box and category
    places.py    what counts as a business, and how its tags map to one record

Data (c) OpenStreetMap contributors, ODbL 1.0. Attribution travels with every value this
produces.
"""

from app.osm.download import Extract, fetch_extract, region_url
from app.osm.index import IndexStats, IndexWriter, OsmIndex
from app.osm.ingest import IngestReport, build_index
from app.osm.places import Place, place_from_tags

__all__ = [
    "Extract",
    "IndexStats",
    "IndexWriter",
    "IngestReport",
    "OsmIndex",
    "Place",
    "build_index",
    "fetch_extract",
    "place_from_tags",
    "region_url",
]
