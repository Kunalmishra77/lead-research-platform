# OpenStreetMap place index

The free discovery backbone (ADR-0016). An extract is downloaded once, reduced to a SQLite place
index on local disk, and queried per search. No API, no quota, no cost per row.

**Data © OpenStreetMap contributors, ODbL 1.0.** Attribution is a licence condition, not a
courtesy: it must appear wherever an OSM-sourced value is shown or exported.

## Why a file and not an API

Measured on 2026-10-10, against the live database:

| Ceiling | Capacity | Binding? |
| --- | --- | --- |
| Supabase free plan, 500 MB (11.8 KB per lead) | ~40,000 leads, **in total** | **yes** |
| Google Places free tier, 1,000 calls/month | ~5-10k unique businesses/month | no |
| Overpass API fair use | not permitted at this volume | no |

Storage binds first, and it binds on the total rather than the month. Loading an India-wide POI
index into Postgres would have spent ~300 MB of the 466 MB available before a single lead existed.
The VPS has 118 GB free. The breadth goes on the disk.

Overpass is not an alternative. Its fair-use policy names this exact pattern — "stitching bounding
boxes" and "building a non-OSM app that relies on the public instances as its backend" — and caps
use at ~10,000 requests and ~1 GB a day. The `504`s and `429`s an exploratory query collected were
the policy working. The same document says what heavy users should do: take an extract. So the free
path and the compliant path are the same one.

## Building the index

```bash
cd services/workers
uv run python -m app.osm --data-dir /data/osm build --region india
uv run python -m app.osm --data-dir /data/osm stats
```

The first command downloads the extract (skipping it when the local copy is already current),
verifies Geofabrik's published MD5, and writes `<region>.sqlite` beside it.

### Regions

Sizes verified against the server on 2026-10-10. A zone answers queries for that zone exactly as
well as the whole country does, for a fraction of the disk and RAM.

| `--region` | Extract | Covers |
| --- | --- | --- |
| `india` | 1631 MB | everything |
| `india-northern` | 213 MB | Delhi, Haryana, Punjab, HP, J&K, Uttarakhand |
| `india-southern` | 532 MB | KA, TN, KL, AP, TG |
| `india-western` | 211 MB | MH, GJ, GA |
| `india-central` | 335 MB | MP, UP, CG |
| `india-eastern` | 236 MB | WB, BR, OD, JH |
| `india-north-eastern` | 105 MB | the north-east |

### Memory

`--location-storage flex_mem` (the default) keeps node positions in RAM: roughly a gigabyte for
India, against the 7.6 GB free on the VPS. Where memory is tighter than time, use
`--location-storage sparse_file_array`, which keeps them on disk instead.

The cache exists for ways. A POI tagged on a building outline — about a fifth of them, measured on
Delhi — has no coordinates of its own, only corners, so the corners have to be known to place it.

### Refreshing

Geofabrik rebuilds every extract daily. A refresh is the same command: it `HEAD`s the file, finds
the local copy current, and transfers nothing. Monthly is enough for business data.

```cron
17 3 1 * *  cd /app/services/workers && uv run python -m app.osm --data-dir /data/osm build --region india
```

**The data directory must be a persistent volume.** Without one, every redeploy re-downloads
1.6 GB and rebuilds the index for no reason.

## What counts as a business

OSM has no "is a business" flag, so `places.py` decides, and that decision is final — nothing
downstream re-examines it, so a car park admitted here becomes a lead and reaches a client.

The primary tags divide in two:

- `shop`, `office`, `craft`, `healthcare` mean commerce by definition. Every value is allowed,
  including values nobody has thought of yet.
- `amenity`, `leisure`, `tourism` are mixed. `amenity=restaurant` is a lead; `amenity=bench` is
  street furniture. These are filtered by a deny list.

The deny list runs that way round on purpose. An allow list of permitted values would silently
drop every category nobody listed, which for a lead-generation tool means silently not finding
customers. A name is required on top, which removes most street furniture by itself.

Categories are stored raw (`leisure=fitness_centre`), not mapped to our taxonomy, so the mapping
can change without re-reading a 1.6 GB file.

## Querying

```python
index = OsmIndex(Path("/data/osm/india.sqlite"))
gyms = index.search(
    bbox=(28.40, 76.83, 28.89, 77.35),          # (min_lat, min_lng, max_lat, max_lng)
    categories=["leisure=fitness_centre"],
    limit=500,
)
```

`bbox` matches `DiscoveryQuery.bbox`, so no projection or distance maths is needed anywhere.
Results are ordered by id, which is what makes paging with `offset` stable across a rebuild.

Queries are synchronous because SQLite is; a caller in the event loop wraps them in
`asyncio.to_thread`. That is cheap next to an index scan on a local file.

### One trap, since it cost a bug

SQLite's R*Tree stores coordinates as 32-bit floats and rounds each stored box **outward** so
queries can never miss an entry. A point at latitude 28.45 is held as roughly
[28.449999, 28.450001]. The index therefore queries for *overlap*
(`max >= query_min and min <= query_max`), never containment: the containment form dropped points
sitting exactly on a box edge, which in a tiled sweep is one missing stripe per tile boundary.

## Caveats

- **Relations are not read.** POIs on Delhi are 18,185 nodes and 4,312 ways; relations are a small
  remainder and reading them means assembling multipolygons. `IngestReport.relations_skipped`
  counts what that leaves behind.
- **Contact coverage is thin.** About 6% of Delhi's OSM businesses carry a website tag. OSM gives
  breadth — name, category, address, often a phone. Emails come from crawling the business's own
  site, as they always did.
- **A snapshot has a date.** `IndexStats.extract_date` is the day OSM cut the data, and every
  value read from the index inherits that freshness. It is not the day we read the file, and it
  must not be displayed as though it were.
