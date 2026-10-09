# ADR-0015: A job reports what it found, not only what was new

- Status: Accepted
- Date: 2026-10-09

## Context

ADR-0012 made `leads` the tenant side of the graph and gave it `research_job_id` — the job that
delivered this lead. One column, and the job page reads it to answer "what did this job find?".

The first live run showed the two are not the same question. A search for *20 dental clinics in
Delhi* found 12 businesses and wrote 179 values. All 12 were already in the workspace, delivered by
an identical search two weeks earlier. `_deliver` inserts `ON CONFLICT DO NOTHING` — deliberately,
because a workspace that has since changed a lead's status must not have it reset, and because
billing charges per *new* lead and the customer already paid for these. So no row was inserted, no
row's `research_job_id` was touched, and the job page said:

```
Businesses seen 12 · Leads delivered 0 · Values stored 179
No leads yet. They appear here as the search finds them.
```

Every one of those numbers is true and the screen is a lie. The customer ran a search, it worked,
and the product told them it found nothing.

The column is being asked to carry two facts that genuinely differ:

- **Which job first delivered this lead.** A fact about the lead. Stable for its lifetime. It is
  what an audit asks and what a deletion request follows.
- **Which jobs surfaced this lead.** A fact about each job. Many jobs can surface one lead, and
  one job surfaces many leads — many-to-many, which no column on `leads` can hold.

Overwriting `research_job_id` on every re-delivery was never an option: it destroys the first fact
to approximate the second, and still shows only the most recent job's results correctly.

## Options considered

1. **Derive it at read time from the job's stored values.** `field_values` records
   `source_id`/`observed_at` per value, so "companies this job observed" is almost reachable by
   joining values written during the job's window. No migration. But `field_values` has no
   `research_job_id` — the join would be on a timestamp range, which is wrong the moment two jobs
   overlap, and values are deduplicated so a re-observation of an unchanged value may write no row
   at all. It infers a fact the system could simply record.

2. **Give `research_tasks.output` the lead ids.** The discovery handler already returns a summary
   per task and the task rows are per job. Cheap. But `output` is a JSON blob for humans reading
   the admin tree; making the grid's pagination, sorting and filtering read through it means
   unpacking JSON to drive a keyset cursor over a few hundred rows. The grid query is indexed and
   cursor-paged for a reason, and this would quietly undo that.

3. **A link table, `app.research_job_leads`.** One row per (job, lead), written whenever a job
   surfaces a lead — new or already held — carrying `is_new` so billing and the UI read the same
   source of truth. One join, indexed both ways. Costs a migration, a table, and one more insert
   per candidate inside a transaction that is already open.

## Decision

Option 3. `app.research_job_leads (org_id, research_job_id, lead_id, is_new, created_at)`, primary
key `(research_job_id, lead_id)`, with an index on `lead_id` for the reverse question ("which
searches turned this company up?" — which is itself worth showing, and is free once the row exists).

`leads.research_job_id` keeps its ADR-0012 meaning untouched: the job that *first* delivered this
lead. Nothing rewrites it. The grid stops reading it and joins the link table instead.

`is_new` is stored rather than recomputed because it is a statement about a moment. "Was this lead
new to the workspace when this job ran?" has exactly one correct answer and it is only knowable at
write time — a later job surfacing the same lead must not change what an earlier invoice said.
`sum(is_new)` is therefore what billing counts, and the same number the UI shows.

The job page shows both: **"12 found · 0 new to your workspace"**. A customer who re-runs a search
sees that their search worked and that they were not charged again. Those are two different pieces
of good news and the screen now has room for both.

The worker gets `INSERT` and no `UPDATE`, for the reason ADR-0012 gave: a task that runs again must
not be able to edit history. The insert is `ON CONFLICT DO NOTHING`, so a retried task is harmless.

## Consequences

Easier: "what did this run find?" and "what did it cost me?" stop being the same query, so either
can change without breaking the other. The reverse lookup — every search that found a given company
— becomes a one-line query, which Phase 6's scoring wants and the lead drawer can show now.

Harder: two writes where there was one, inside the per-candidate transaction. Measured against a
discovery task's HTTP call it is noise, and it shares the transaction so a crash cannot leave a
lead delivered but unlinked.

Backfill: every existing `leads` row with a non-null `research_job_id` gets a link row with
`is_new = true`. That is accurate — those rows exist *because* they were new. Jobs before this
migration that re-found an existing lead left no trace anywhere, and the migration does not invent
one; their counts stay as they are.

Follow-up: `research_jobs.progress` currently counts delivered leads from the worker's own tally.
It now counts link rows for "found" and `is_new` for "new", so the live SSE counter and the final
page agree instead of differing by every re-found lead.
