# ADR-0016: volume comes from a file on disk, not an API quota

- Status: Proposed
- Date: 2026-10-10

## Context

The owner needs lakhs of leads a month, for several clients, at zero cost, and explicitly does
not want the product to depend on any single source.

The obvious reading is that Google Places is the bottleneck. It is not. Measured on 2026-10-10,
against the live database:

```
leads                657
companies            641
field values      13,870   (21.1 per lead)
per-lead storage    11.8 KB

database used         34 MB of 500 MB (Supabase free plan)
headroom             466 MB
=> room for       40,645 more leads, in total
```

Three ceilings, in the order they bite:

| Ceiling | Capacity | Binding? |
| --- | --- | --- |
| Supabase free plan, 500 MB | **~40,000 leads, ever** | **yes** |
| Google Places free tier, 1,000 calls/month | ~5-10k unique businesses/month | no |
| Overpass API fair use | not permitted at this volume | no |

Storage is two orders of magnitude short of the requirement, and it is a ceiling on the *total*,
not on the month. Fetching more leads from more sources would have filled it and stopped, which
is a worse failure than a quota: a quota resets.

The second finding is that the free alternative everyone reaches for first is not usable.
OpenStreetMap's Overpass API fair-use policy names our exact pattern as abuse — "stitching
bounding boxes to download the whole world" and "building a non-OSM app that relies on the public
instances as its backend" — and caps use at roughly 10,000 requests and 1 GB a day. The `504` and
`429` responses an exploratory query collected were the policy working, not a fault. The same
document says what heavy users should do instead: take the planet dump or a regional extract.

So the free path and the compliant path are the same path, and it is not an API.

## Options considered

1. **Query Overpass per search.** No setup. But it is the thing the policy forbids at this
   volume, it makes a donated public service our production backend, and it would be the single
   dependency the owner asked not to have.

2. **Pay for Supabase Pro ($25/month, 8 GB).** ~700,000 leads at rest, nothing to prune, full
   history searchable. Simple and honest, and the right answer the day the product earns money.
   Rejected for now only because the constraint is zero spend.

3. **Load the OSM extract into Postgres.** Spends the scarce resource on the abundant one: an
   India-wide POI index is ~300 MB of the 466 MB we have, before a single lead is stored.

4. **Keep the bulk data as a file on disk, and make stored leads transient.** The extract lives
   on the VPS, which has 118 GB free of 193 GB and 15 GB of RAM. Leads occupy Supabase only
   between discovery and export.

## Decision

Option 4, in three parts.

**Discovery reads a file, not an API.** Geofabrik's `india-latest.osm.pbf` (1.59 GB, rebuilt
daily; verified by `Content-Length` and `Last-Modified` on 2026-10-10) is downloaded to the VPS
and reduced to a lean POI index — name, category, point, address, phone, website — in a SQLite
file beside it, with an R*Tree index for bounding boxes. The index is on disk, not in Postgres,
because Postgres is the resource we are short of and the disk is the one we have 118 GB of.
There is no request quota on a local file, and no third party to be throttled by.

**Leads are transient; the fact that we delivered them is not.** A lead holds 11.8 KB because
every value carries provenance, which is not negotiable (CLAUDE.md). But the product's job is to
*deliver* leads, not to store them: the client receives a CSV. So after export, a lead's values
are pruned and a lean row is kept recording that this business was already delivered to this
workspace — roughly 100 bytes.

```
466 MB / 11.8 KB  =     40,000 leads held in full   (transient)
466 MB /    100 B =  4,600,000 deliveries remembered (permanent)
```

That asymmetry is the whole design. Forty thousand slots that leads flow through, and a memory
of four million-odd deliveries, is lakhs a month without ever sending a client the same business
twice. Storage stops being a ceiling on the total and becomes a ceiling on work in progress.

**Places stops being the discovery engine and becomes enrichment.** OSM has the breadth (22,497
named businesses in Delhi alone, measured 2026-10-09) and thin contact data (6% carry a website
tag). Places has the opposite. Spending the 1,000 free Places calls on businesses OSM already
found, rather than on finding them, is worth more per call. This requires the planner to use
more than one discovery source, which today it cannot: `_discovery_source()` takes `next()` of
the sources sorted by cost, so registering a zero-cost connector would silently replace Places
rather than join it.

### Why this is not a licence problem

OSM is ODbL 1.0. Attribution — "data © OpenStreetMap contributors, ODbL 1.0" — is already
required for the geography seed (docs/08) and must now also appear wherever an OSM-sourced value
is shown or exported. Every OSM value records `source_url` as the element's own page
(`https://www.openstreetmap.org/node/<id>`), so any single fact a client questions can be opened
and checked by a human. `method` is `provider`: this is a third-party dataset, not a call we
made and not something a model produced.

## Consequences

Easier:

- Discovery volume is bounded by disk and CPU, not by anyone's quota, and costs nothing.
- No single source. If Places is unavailable, exhausted, or priced out, discovery still works.
- Phase 4 resolution finally has something to resolve. With one discovery source, "verify a value
  across sources" had no second opinion to consult; with two it does.
- Phase 2's last open criterion — 200 unique candidates for Delhi, unmeasured because it costs
  about a dollar of Places that was never funded — can be measured for nothing.

Harder, and the follow-up work this creates:

- **A monthly refresh job.** A file snapshot goes stale in a way an API does not. Stale rows must
  be visible as a freshness date, not hidden.
- **A prune sweeper**, and a decision about its window. Pruning is destructive; it needs the same
  care as the Places 30-day sweeper (ADR-0011), and until it exists the 40,000 ceiling is real.
- **The delivered ledger has to be right.** If it is wrong in one direction a client gets
  duplicates; in the other, a lead can never be delivered again. It is small but it is now the
  only permanent record that a business was ever sent to anyone.
- **Coolify needs a persistent volume** for the extract and the index, or every redeploy
  re-downloads 1.59 GB.
- **A lead the owner exported and pruned is gone from the product.** They keep the CSV; the
  workspace keeps a name and an id. This was the owner's explicit choice over $25/month
  (2026-10-10), and it should be revisited the day the product has paying clients: option 2 turns
  pruning off by changing a setting, not by rewriting this.
