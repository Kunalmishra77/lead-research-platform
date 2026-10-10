# The place index

The free discovery layer (ADR-0016, ADR-0017). A bulk dataset is extracted once into a SQLite
index on local disk and queried per search. No API, no quota, no cost per row, and nobody who can
turn it off.

Two datasets feed it:

- **Overture Maps Places** — the discovery source.
- **OpenStreetMap** — the second opinion Phase 4 verifies values against.

## Why these, measured

Everything below was measured, not estimated. Same Delhi bounding box for both:

| | OSM | **Overture** | Our paid Google pipeline |
| --- | --- | --- | --- |
| businesses | 21,879 | **283,302** | ~5-10k/month |
| with their own phone | 9.7% | **67.1%** (190,150) | 74% |
| with their own email | 2.7% | **38.0%** (107,740) | 8% |
| cost | ₹0 | **₹0** | ₹2.21 per contactable lead |
| ceiling | none | **none** | 1,000 calls/month |

"Own" rather than "filled" is the honest measure. 80.1% of Overture rows have a phone field, but
200 numbers cover 16,099 places — chains repeating one switchboard — so counting filled fields
overstates reachable businesses by about a fifth.

For scale: OSM across **all of India** holds 639,557 businesses, about 62,000 with a phone.
Overture in **Delhi alone** has three times that many reachable.

### Why a file rather than an API

The Supabase free plan gives 500 MB and a lead costs 11.8 KB of it, so the database holds about
forty thousand leads in total. Storage binds two orders of magnitude before any API quota does.
Putting bulk data in the scarce store would spend the product's capacity before a lead existed,
while the VPS has 118 GB free. So breadth lives on disk, and Postgres holds only leads being
worked on.

Overpass is not an alternative. Its fair-use policy names this exact pattern — "stitching
bounding boxes", "building a non-OSM app that relies on the public instances as its backend" —
and caps use near 10,000 requests a day. It says to take an extract instead, so the free path and
the compliant path are the same path.

## Building

```bash
cd services/workers
uv run python -m app.places --data-dir /data/places build-overture        # discovery
uv run python -m app.places --data-dir /data/places build-osm --region india   # second opinion
uv run python -m app.places --data-dir /data/places stats
```

`build-overture` reads GeoParquet straight from public object storage with DuckDB; the bounding
box is pushed into the Parquet row-group statistics, so extracting India never downloads the rest
of the world. It defaults to India and takes `--bbox MIN_LAT MIN_LNG MAX_LAT MAX_LNG` for less.

`build-osm` downloads a Geofabrik extract, verifies its published MD5, and streams it through
osmium. Sizes verified 2026-10-10: `india` 1631 MB, `india-northern` 213 MB, `india-southern`
532 MB, `india-western` 211 MB, `india-central` 335 MB, `india-eastern` 236 MB,
`india-north-eastern` 105 MB. Real run: 213 MB in, 82 seconds, 76,153 businesses, 15.8 MB index.
All of India: 639,557 businesses, 133 MB, 54 minutes.

**The release is pinned, not "latest".** A release is a dated snapshot and every value read from
it inherits that date. Resolving "latest" at runtime would silently change what a rebuild
produces and make two indexes built a week apart impossible to tell apart.

**The data directory must be a persistent volume.** Without one every redeploy re-downloads
gigabytes and rebuilds for nothing.

```cron
17 3 1 * *  cd /app/services/workers && uv run python -m app.places --data-dir /data/places build-overture
```

### Memory

OSM's `--location-storage` defaults to `flex_mem`, which keeps node positions in RAM — about a
gigabyte for India against 7.6 GB free on the VPS. Use `sparse_file_array` where memory is
tighter than time. The cache exists for ways: a POI tagged on a building outline has no
coordinates of its own, only corners.

## What counts as a business

Nothing downstream re-examines this, so a car park admitted here becomes a lead, gets crawled,
and reaches a client.

The test is **could anything ever be sold to it** — narrower than "is it a business". A temple
buys sound systems and catering and a petrol pump is a company, so both stay in. An ATM is a
machine belonging to a bank already indexed under its own name, so it does not; it also carries
the bank's national helpline, which would pad both the lead count and the apparent phone
coverage with a number nobody wants to be called on.

For OSM, `shop`, `office`, `craft` and `healthcare` mean commerce whatever the value; `amenity`,
`leisure` and `tourism` are mixed and filtered by a deny list. That way round on purpose: an
allow list silently drops every category nobody thought of, which for a lead-generation tool
means silently not finding customers.

Overture also publishes `operating_status`, which is respected. A closed business with a perfect
phone number is a worse lead than no lead.

## Corrupted rows

Some upstream Overture rows are column-shifted. A real Delhi example had `IN-HR` in phone, `IN`
in email and `122004` in website — a region code, a country and a postcode, each one field left
of where it belonged. 1,327 of 226,918 Delhi phone values, 0.6%.

Every contact value is therefore checked against its own shape and dropped if it fails, keeping
the rest of the record. Validating only the phone and trusting the others is how a postcode ends
up stored as a company's website. A junk value is worse than a missing one: missing is honestly
incomplete, wrong sends someone to a dead end and looks like our mistake.

## Querying

```python
index = PlaceIndex(Path("/data/places/overture.sqlite"))
gyms = index.search(
    bbox=(28.40, 76.83, 28.89, 77.35),   # (min_lat, min_lng, max_lat, max_lng)
    categories=["gym"],
    source="overture",
    contactable=True,
    limit=500,
)
```

`bbox` matches `DiscoveryQuery.bbox`, so no projection or distance maths is needed anywhere.
Results are ordered by id, which is what makes paging with `offset` stable across a rebuild.
Queries are synchronous because SQLite is; a caller in the event loop wraps them in
`asyncio.to_thread`.

### One trap, since it cost a bug

SQLite's R*Tree stores coordinates as 32-bit floats and rounds each stored box **outward** so
queries can never miss an entry. A point at latitude 28.45 is held as roughly
[28.449999, 28.450001]. The index therefore queries for *overlap*
(`max >= query_min and min <= query_max`), never containment: the containment form dropped points
sitting exactly on a box edge, which in a tiled sweep is one missing stripe per tile boundary.

The same class of mistake twice over: a closed OSM way repeats its first node, so averaging the
list as given put every building's pin about 110 m off in the same direction; and an Overture
place is pinned at the centre of its bbox, not a corner.

## Licences

These travel with the data and are not optional.

- **Overture**: CDLA-Permissive-2.0 for most sources, Apache 2.0 for Foursquare-derived rows, CC0
  for AllThePlaces. Commercial use including in a paid product is permitted, with no royalty and
  no attribution requirement — but §2.1 says sharing the Data obliges us to make the agreement
  text available with it, so any export built from it must carry
  `docs/licenses/CDLA-Permissive-2.0.md`. §3.1 exempts Results — a score, a segment, an insight —
  but a row of names and phone numbers is Data and gets no exemption.
- **OpenStreetMap**: ODbL 1.0. "Data © OpenStreetMap contributors, ODbL 1.0" wherever its values
  are shown or exported.

## Caveats

- **Coverage is measured; accuracy is not.** Whether a number is current cannot be known without
  dialling it. True of every source, Google included, and what Phase 4 verification is for — now
  with two datasets to compare.
- **Chains.** 200 numbers cover 16,099 Delhi places. A client sent forty branches of one bank
  with one switchboard number has been sent one lead and thirty-nine duplicates.
- **OSM relations are not read.** Delhi POIs are 18,185 nodes and 4,312 ways; relations are a
  small remainder and reading them means assembling multipolygons.
- **A snapshot has a date.** `IndexStats.extract_date` is when the data was cut, not when we read
  it, and must not be displayed as though it were.
