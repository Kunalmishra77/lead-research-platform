# ADR-0019: the raw store is a disk, the metadata is the database

- Status: Accepted
- Date: 2026-10-13

## Context

Task 3.6 asks for the crawler's raw responses to be kept: `raw_documents` rows plus the body in
S3 under `raw/{yyyy}/{mm}/{url_hash}/{fetched_at}.html.gz`, deduplicated by content hash
(docs/04, docs/06 §4.9). Neither the table nor the store exists; `field_values.raw_document_id`
has been a nullable column pointing at nothing since migration 0013.

ADR-0002 predicted the problem this hits, in its own words: *"Free limits (500 MB DB, 1 GB
storage, 100 ops/s Redis) are fine for Phase 1-2 but not for Phase 3 crawling; plan paid tiers
before Phase 3."* The plan was to pay. The owner's constraint since is that nothing may cost
money, which is the same constraint ADR-0016 answered for discovery by reading a file on disk
instead of metering an API.

Measured rather than estimated, on 2026-10-13:

| | |
| --- | --- |
| crawl tasks run | 607 |
| ... that read no page at all (robots, restricted, dead host) | 348 |
| ... that read a page and extracted something | 238 of 257 |
| pages fetched in total | 351, so **0.58 per crawl task** |
| page size, 12 real sites from the graph | median **30 KB** gzipped (161 KB raw) |
| leads with a website, so crawlable | 589 of 1,550, **38%** |

Projecting the owner's target of lakhs of leads a month:

```
1 lakh leads  ->  38,000 crawlable  ->  22,000 pages  ->  ~660 MB gzipped
Supabase Storage, free plan: 1 GB
```

So the free object store is exhausted at about 1.5 lakh leads *ever*, and docs/04's 90-day object
retention does not save it: at two lakh leads a month the rolling window is roughly 4 GB.

Storing less was considered first and does not help. Keeping only the pages a value came from
saves 7%: of the 257 crawls that read a page, 238 extracted something. The 348 that read nothing
already store nothing, because there is nothing to store.

The same machine that runs the workers has **134 GB free** of a 193 GB disk, and already carries
a 245 MB Overture index on a named volume for exactly this reason (ADR-0016).

## Options considered

1. **Supabase Storage, as docs/06 says.** One line of config, durable, replicated. Full at ~1.5
   lakh leads, which is under one month of the target.
2. **A paid object store** (Supabase Pro, Backblaze B2, Cloudflare R2). R2 has a 10 GB free tier
   and no egress fee, which would genuinely last — but it is an account, a card and a key the
   owner has said no to, and the egress-free part only matters when something reads these often,
   which nothing does.
3. **Store the extracted main text instead of the HTML.** About 2 KB gzipped, 15x smaller, and it
   is what the LLM extraction in 3.11 consumes. But it is not what the other extractors consume:
   JSON-LD and microdata, `mailto:`/`tel:` hrefs, social anchors and tech fingerprints all live
   in the markup, and those are the ones that produce contact values today. Re-extraction would
   be impossible for exactly the fields that matter.
4. **Rows only, no bodies.** Free and keeps content-hash dedupe and "has this page changed since
   we read it", at about 200 bytes a page. Loses re-extraction without re-fetching, which for
   22,000 sites is days of polite crawling, and loses the one thing that answers a customer
   disputing a value after the page has changed.
5. **A local volume on the worker's own disk**, with the row in Postgres. Free, 134 GB, and the
   precedent is in the repository.

## Decision

**Option 5.** The `raw_documents` row goes in Postgres exactly as docs/04 specifies, partitioned
monthly, and the body goes to a local volume under the key docs/06 already defines. docs/06 §4.9
is amended to say "the raw store" rather than "S3", and the store is written behind a small
interface with two implementations so that a paid object store is a configuration change rather
than a rewrite.

What makes a local disk the right answer here rather than a compromise: these objects are a
90-day cache of public web pages. They are re-fetchable, they are not customer data, and nothing
depends on them being there. Losing the volume costs a re-crawl, not a customer's leads. That is
a very different durability requirement from `field_values`, which is in Postgres and backed up.

The row is written whether or not the body is, because the row is what carries the evidence:
`url_hash`, `content_hash`, `http_status`, `bytes`, `access_status` and `fetched_at` together say
"this URL answered with exactly these bytes at this time", and a `content_hash` is proof a page
has not changed since without keeping a copy of it.

Cross-partition uniqueness follows ADR-0003: `unique(url_hash, content_hash)` cannot be enforced
across partitions of a table keyed by `fetched_at`, so it lives in a small non-partitioned
`raw_document_keys` table, which is also what `field_values.raw_document_id` can point at. That
foreign key is `ON DELETE SET NULL`: when a document is pruned the value keeps its `source_url`
and loses only the copy, which is exactly what docs/04's retention describes.

## Consequences

- One named volume on the worker host, `raw-data` at `/data/raw`, beside `places-data`. It has to
  be declared in the compose file or the store silently writes into the container's own layer and
  disappears on the next deploy.
- 90-day object retention is a worker job deleting files, not a bucket lifecycle rule. Row
  metadata is kept 12 months by a sweeper in the pattern of 0017 and 0027.
- The store is per host. A second worker host would not see the first one's objects, so a
  re-extraction job has to run where the pages were written, or the store has to move to option 2
  first. Written down here because it is the thing that will bite when workers scale out (Phase
  10), and it is the honest cost of this choice.
- Google Places bodies stay out of this store entirely (ADR-0011): their content has a 30-day
  life and a separate deletion clock, and mixing them into a 90-day store would quietly break it.
