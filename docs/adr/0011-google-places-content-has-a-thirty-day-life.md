# ADR-0011: Google Places content lives for 30 days, place IDs forever

- Status: Accepted
- Date: 2026-09-23

## Context

Phase 2 discovers businesses through the Google Places API (New). The product stores what it finds
as `field_values` with provenance and exports it (docs/00, docs/06), and Phase 2's acceptance
criterion asks for candidates "with name, address, phone/website where Places provides them, each
value with provenance".

Google's terms do not allow that indefinitely. Verified in the official documentation on
2026-09-23:

- **Maps Service Specific Terms, Places API:** "Customer may temporarily cache latitude and
  longitude values from the Places API for up to 30 consecutive calendar days, after which
  Customer must delete the cached latitude and longitude values."
- **Maps Platform Terms §3.2.3(b), No Caching:** "Customer will not cache Google Maps Content
  except as expressly permitted under the Maps Service Specific Terms."
- **Places policies:** "the place ID, used to uniquely identify a place, is exempt from the
  caching restrictions. You can therefore store place ID values indefinitely." Google recommends
  refreshing IDs older than 12 months, which is free through an IDs-only Details call.
- **Attribution:** Places content shown to a user must carry Google attribution, visible and
  unmodified.

So the one field the terms name for Places — lat/lng — gets 30 days, place IDs get forever, and
everything else (name, address, phone, website, rating, hours, business status) is Google Maps
Content under the no-caching rule.

## Options considered

1. **Discovery only.** Keep place IDs and lat/lng; use the rest transiently inside a job to seed
   the Phase 3 crawler, and let the company's own website become the source of record. Cleanest
   against the terms and cheaper (the IDs-only SKUs are free), but Phase 2 then cannot deliver a
   name or a phone number until crawling exists, which is Phase 3.
2. **Store with a 30-day life.** Persist Places-derived values like any other value, with a hard
   30-day expiry, a sweeper that deletes them, and Google attribution wherever they are shown.
   Phase 2 delivers what it promised; the obligation moves into the data lifecycle, and exports
   put copies outside our control.
3. **Licence the data elsewhere.** A listings vendor whose terms permit storage. Out of scope now.

## Decision

Option 2, chosen by the product owner. Places-derived values are stored, and are treated as
perishable:

- `sources.default_ttl_days = 30` for `google_places`. The connector declares the same, and
  nothing derived from Places may be given a longer life.
- Place IDs are the exception and are stored indefinitely, as the external id on the company's
  source link — they are what makes a refresh cheap and a duplicate avoidable.
- A sweeper deletes (not merely hides) `field_values` from a source once
  `observed_at + sources.default_ttl_days` has passed. Deriving the deadline from `observed_at`
  means no schema change; the sweeper is required before this data reaches production.
- Anything shown to a user that came from Places carries Google attribution (docs/09).
- Fixtures in this repo carry the API's **shape** with synthetic values. Committing real Places
  content to git would itself be storage beyond the permitted window, in a place no sweeper can
  reach.

## Consequences

- A lead delivered from Places alone is worth less after 30 days, which is the honest position:
  the durable record comes from the company's own website once Phase 3 crawls it, and a Places
  value should be replaced by a crawled one wherever both exist.
- The sweeper is now a shipping requirement, not a nicety. Until it exists, this source must not
  run against real customer data.
- Exports copy values outside our deletion reach. Phase 7 has to decide what an export of
  Places-derived data means; the options are to exclude it, to mark it, or to re-verify before
  exporting.
- `place_id` is the join key for re-discovery, so a re-run costs a free IDs-only call rather than
  a paid search.
- If the 30-day obligation ever proves unworkable in practice, option 1 remains available without
  changing the connector: it is the same calls with less written down.
