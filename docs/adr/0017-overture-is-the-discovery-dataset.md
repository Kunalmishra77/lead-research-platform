# ADR-0017: Overture is the discovery dataset; OpenStreetMap becomes the second opinion

- Status: Proposed
- Date: 2026-10-10
- Amends: ADR-0016 (whose decision stands; the choice of which file changes)

## Context

ADR-0016 established that volume has to come from a bulk file rather than an API quota, and that
decision survives this one intact. It then picked OpenStreetMap, and that part was wrong.

The owner's objection is what found it: *leads you cannot contact are not leads*. Measured on the
Delhi bounding box, OSM is **9.7% phone and 2.7% email**. A list of 21,879 businesses of which
roughly 2,100 can be phoned is not a lead list, whatever its licence costs.

Researching properly — which should have happened before writing any ingest code — turned up two
free bulk datasets built from commercial sources rather than volunteer mapping:

- **Overture Maps Places**, CDLA-Permissive-2.0, 64M+ places, monthly, assembled by Meta,
  Microsoft, Foursquare, TomTom and others.
- **Foursquare OS Places**, Apache 2.0, 100M+ places, monthly.

Measured on the same Delhi box as OSM, Overture gives:

```
named places                          286,277
  machines/infrastructure (ATM, …)      2,975   1.0%
  businesses                          283,302

of those businesses:
  phone field filled                  226,918  80.1%
  … not actually a phone number         1,327   0.6%  (column-shifted upstream rows)
  with their OWN phone number         190,150  67.1%
  with their OWN email                107,740  38.0%
```

"Own" matters more than "filled". A first sample looked alarming — five of eight rows were ATMs
sharing one bank helpline — but that sample was ordered by confidence, and banks score 1.00. The
full count puts infrastructure at 1.0% and corruption at 0.6%, and 200 shared numbers account for
16,099 places. The chain-head-office problem is real and small.

For comparison, our paid Google Places pipeline gives phone for 74% and email for 8% of what it
delivers, at ₹2.21 per contactable lead and a free ceiling near 1,000 calls a month. Overture's
email coverage is five times better than ours, because ours comes only from crawling and 49% of
sites refuse the crawler.

## Options considered

1. **Keep OSM as the discovery source.** Already built and tested. Rejected: it does not answer
   the question the product exists to answer.
2. **Foursquare OS Places.** More places and a simpler licence (Apache 2.0). Access now runs
   through the Places Portal, Hugging Face or Snowflake, each needing an account and approval,
   where Overture is an anonymous read from public object storage. Worth measuring later as a
   complement; not the first one to build against.
3. **Overture Maps Places.** Public S3, no account, queryable in place with DuckDB so an area can
   be extracted without downloading the world, per-property provenance in the data itself, and
   contact coverage sixty times OSM's.
4. **Scrape Google Maps.** What the "lead scraper" repositories do. Refused: it breaks Google's
   terms, needs bot-detection evasion, and hands a business built on it a single cease-and-desist
   as a point of failure. CLAUDE.md forbids it and that is not being revisited.

## Decision

**Overture Places becomes the primary discovery dataset.** The connector built for Phase 2 queries
it, not OSM.

The machinery from ADR-0016 carries over unchanged, which is why that ADR was not a waste: the
local index with an R*Tree, the overlap-not-containment query, the deny list separating businesses
from infrastructure, the staged build and atomic rename, and the export-and-prune storage design
that makes a 500 MB database pass lakhs of leads through 40,000 slots. Only the reader changes —
DuckDB over GeoParquet instead of osmium over a PBF.

**OpenStreetMap stays, as the second opinion.** Phase 4 asks whether a value is right, and with one
discovery source there was never anything to ask. Two independently produced datasets disagreeing
about a phone number is a signal worth having, and OSM's 21,879 Delhi businesses overlap Overture's
283,302 heavily enough to be useful for exactly that.

**Provenance comes from the data.** Each Overture row carries `sources` with `dataset`, `license`,
`record_id`, `update_time` and `confidence` per property, so `source_url`, `observed_at` and
`confidence` are recorded rather than invented. `method` is `provider`. Rows whose phone fails a
format check are dropped at ingest, not stored and explained later.

**Licence obligations.** CDLA-Permissive-2.0 §2.1: sharing the Data requires making the agreement
text available with it, so any export containing Overture-derived values ships
`docs/licenses/CDLA-Permissive-2.0.md`. §3.1 exempts Results — a score, a segment, an insight —
but a row of names and phone numbers is Data and gets no exemption. Foursquare-sourced rows are
Apache 2.0 and need the NOTICE; AllThePlaces rows are CC0 and need nothing. The licence text is
checked in rather than linked, because an export's obligation cannot depend on cdla.dev being up.

### Correcting ADR-0016

ADR-0016 claims that spending Places calls on businesses OSM already found is worth more per call
than using Places to find them. **That is wrong.** Places Text Search returns a business and its
phone in one billed call; getting contacts for a business found elsewhere needs Place Details,
which is billed too. Discovering elsewhere saves no Places calls. The conclusion it supported —
Places stops being the discovery engine — still holds, but for a different reason: a free dataset
with 67% own-phone coverage makes the paid one unnecessary for discovery, not cheaper.

### Correction, from building it end to end

An earlier draft of this ADR said 0.6% of phone values were corrupt, from 1,327 of 226,918
failing a check. That number was wrong in two ways and is withdrawn.

It came from a stricter pattern than the one shipped -- it rejected `+91 124 4567890 x12` and
dotted formats, which are real numbers written normally -- and it counted rows before the
business filter, where most of the damage sits. Building the Delhi index for real refused **13**
phone values among otherwise-valid businesses, and the finished index holds **199,421 distinct
phone values, none of which fail even that strict pattern**.

The corruption is real; the `IN-HR` row exists and the validator is worth keeping. It is rare,
and it concentrates in rows already being refused for other reasons.

The end-to-end build, for the record (Delhi box, 210 seconds, 139.8 MB index):

```
rows read                286,277
refused, not a business   16,968   (machines, and operating_status = closed)
refused, bad phone            13
businesses               269,309
  their own phone        190,145   70.6%
  their own email        107,735   40.0%
  website                152,532   56.6%
  contactable             231,788   86.1%
```

The percentages are higher than the figures above them because refusing the junk shrinks the
denominator, which is the point of refusing it. Gurgaon alone: 526 gyms, 476 of them contactable,
carrying branch-level addresses like `sec89gurugram@plus.fitness` rather than a head office.

## Consequences

Easier:

- The product's central promise becomes true. Delhi alone holds 190,150 businesses with their own
  phone number and 107,740 with their own email, free, with no quota and no third party able to
  turn it off.
- Google Places becomes optional. It stays registered for areas or categories where Overture is
  thin, inside its free tier, and the product no longer stops working if it is unavailable.
- Email coverage improves five-fold over the current pipeline without crawling anything, which
  also means fewer requests at sites that would have refused them.

Harder:

- **Accuracy is unmeasured.** Coverage is counted; whether a number is current is not, and cannot
  be without dialling it. This is true of every source, Google included, and it is what Phase 4
  verification is for — now with two datasets to compare.
- **`operating_status` has to be respected.** A closed business with a perfect phone number is a
  worse lead than no lead.
- **Chains need handling.** 200 numbers cover 16,099 places. A client sent forty branches of one
  bank with one switchboard number has been sent one lead and thirty-nine duplicates.
- **A monthly snapshot has a date**, and the freshness shown to a user must be the dataset's, not
  the day we read it.
- **The 0.6% of corrupted rows must be dropped**, not stored. `IN-HR` in a phone column is a
  column-shift upstream, and a validator at ingest is the only place it is cheap to catch.
- The OSM ingest is now a second-opinion path rather than the main one, and should not be
  presented in the UI as a primary source of leads.
