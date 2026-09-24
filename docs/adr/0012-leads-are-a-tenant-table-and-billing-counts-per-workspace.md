# ADR-0012: Leads are a tenant table, and a "new lead" is new to the workspace

- Status: Accepted
- Date: 2026-09-24

## Context

Task 2.11 put the first real businesses into the graph: a live Delhi run stored 86 companies with
provenance on every value. Then task 2.12 went to display them and found there is nothing to
display *from*.

`companies`, `company_locations` and `field_values` are global (ADR-0007). Nothing records which
research job found a company, or which workspace is entitled to see it. Three things follow, and
the second is the serious one.

**A job's results cannot be read back.** `GET /app/research/:id` returns status, counters and
credits. There is no query that answers "which businesses did this job find", so the job page has
no rows and the phase's own acceptance criterion — "returns ≥ 200 unique candidates for Delhi …
each value with provenance" — cannot be observed per job.

**A second workspace running the same search gets nothing and pays nothing.** The executor's
`is_new` means "this `google_place_id` was not already in the global graph". Verified against the
live database:

```
org A finds it first   : is_new=True   -> charged
org B runs same search : is_new=False  -> charged nothing
same company row       : True
```

Org B's job completes with zero leads. They ran a search, we spent their budget on Places calls,
and they received nothing. That is not a pricing question, it is a broken product: the work was
done and the answer was withheld. It also caps revenue at whatever the first customer in a market
happened to search for.

**`leads` already exists on paper.** docs/04 specifies it, and ADR-0007 refers to `leads.overrides`
as the place user corrections live. It is scheduled for task 4.8, two phases away.

## Options considered

1. **Leave it to Phase 4.** Phase 2 stays inside its boundary; the job page shows counters only.
   Honest about scope, but ships a product where a search returns a number and no rows, and leaves
   the billing defect in place for two phases.
2. **Build the link only** — `org_id, workspace_id, company_id, research_job_id` and nothing else.
   Smallest thing that works, in the spirit of ADR-0007 ("build only what this phase reads").
   Phase 4 then migrates the same table again to add its own columns.
3. **Build `leads` as docs/04 specifies it**, including the columns Phase 4-6 will read.

## Decision

**Option 3**, chosen by the product owner.

This is a deliberate departure from ADR-0007, which decided the opposite for the graph tables:
build only what the phase reads, and add the rest with the phase that uses it. The reasoning
there still holds in general — columns nothing reads are columns nothing tests — and it is
recorded here so the inconsistency is visible rather than accidental. The counter-argument
accepted is that `leads` is a tenant table with a settled specification in docs/04, so a second
migration over it buys nothing, and the unused columns are plain types with no extension
dependencies (unlike the `citext` / PostGIS / `vector` columns ADR-0007 deferred).

**A "delivered new lead" is new to the workspace, not new to the graph.** `app.credit_rates`
prices `research_quick|standard|deep` per "delivered new lead" (docs/11). Counting against the
global graph means a customer pays only for businesses no other customer has ever found, which is
neither what they received nor what the meter says. Counting per workspace means each customer
pays for the leads *they* were given, and two customers in the same market each get a full answer.

The graph is still shared: the second workspace's job reuses the existing `companies` row and its
`field_values` rather than duplicating them. What is new is the `leads` row that connects that
company to that workspace. So the saving from a shared graph is a saving on *our* API spend, not
a discount we are forced to pass on.

**Status values are provisional.** docs/04 names a `status` column but no values, and docs/09 only
shows it as a grid column and a bulk action. `app.lead_status` starts at `new, contacted,
qualified, rejected` — the smallest progression that is uncontroversial. Phase 4 owns the lead
workflow and may add values, which is an additive `ALTER TYPE`; removing one is not, so the set
starts small deliberately.

## Consequences

- Task 2.11's executor gains a tenant write: one `leads` row per discovered company, and `is_new`
  becomes "not already a lead in this workspace". The graph write is unchanged and still public-
  sources-only, so ADR-0007's tenant rule holds — `leads` is the tenant side, `companies` is not.
- The executor needs the workspace, which it reads from `research_jobs` rather than trusting the
  envelope: the job row is what the API wrote when the user pressed run.
- `GET /app/research/:id/results` becomes answerable, and is what the job page reads.
- Credits: a workspace re-running its own search still pays nothing for leads it already has,
  because the unique key is `(workspace_id, company_id, person_id)`. Only genuinely new rows are
  charged, and the usage unit key keeps a retried task from charging twice.
- Score, `score_breakdown`, `assignee_user_id`, `overrides`, `custom_fields` and `contacted_at`
  are written by nothing until Phases 4-6. They are nullable or defaulted, and no code reads them,
  so an empty column is the only cost.
- `person_id` is nullable and stays null through Phase 2; the unique index uses
  `coalesce(person_id, uuid_nil())` so one company yields one lead per workspace until people
  arrive in Phase 5.
