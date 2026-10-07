# ADR-0014: A finished job settles itself in Postgres, not in an app process

- Status: Proposed
- Date: 2026-10-07

## Context

Phase 2 cannot close. Its third acceptance criterion reads "Credits: reservation shown before run,
consumed per delivered candidate, unused released", and the live database says otherwise. Every job
that has ever run:

| status | depth | candidates | leads | reserved | used | settled |
| --- | --- | --- | --- | --- | --- | --- |
| completed | quick | 12 | 7 | 20 | 0 | never |
| completed | standard | 175 | 106 | 80 | 0 | never |
| completed | standard | 130 | 88 | 80 | 0 | never |

`app.usage_events` is right: `research_standard` holds 194 units worth 582 credits and
`research_quick` 7 units worth 7. The worker recorded what should be charged. `app.credit_ledger`
holds two grants, one reserve of 20, and **no `consume` or `release` row at all**. So 589 credits of
delivered work has never reached the ledger, and three reservations are still held against balances
that will never get them back.

`app.credit_settle` is not broken. It reads the job's usage, caps it at the reservation, posts the
`consume` and the `release`, and sets `settled_at` and `credits_used`. Nothing calls it.

That is by design, and the design is right as far as it goes. The worker's grant deliberately
excludes the credit columns and `credit_settle` is granted to `app_api` only (migration 0015,
ADR-0003), so a worker can say a job stopped and cannot say what it cost. The API settles on cancel
and on `failAndSettle`. A job the worker finishes normally — the common case — is settled by nobody.

The gap was recorded against task 2.11 rather than papered over, which was right. It is now
confirmed in production with three jobs and a balance to point at, and it blocks the phase.

## Options considered

1. **Grant `credit_settle` to `app_worker`.**
   Pros: one line.
   Cons: removes the only thing standing between a compromised or buggy worker and every tenant's
   balance. The worker handles third-party responses all day; it is the process most likely to be
   fed something hostile. This boundary is the reason the bug exists, and it is worth more than the
   bug costs.

2. **Worker asks the API to settle, over Redis streams** (the mirror of ADR-0005).
   Pros: keeps the grant where it is; reuses a transport that already exists.
   Cons: puts a stream consumer inside a web process, and makes money depend on two processes being
   up at the same moment. A settlement lost because the API was restarting is a silent accounting
   error — the worst kind.

3. **A sweeper inside the NestJS API.**
   Pros: no new infrastructure.
   Cons: needs a singleton among API replicas, so it needs a lock, so it needs the thing it is
   trying to avoid. And settlement stops whenever the API is down, which is exactly when a job is
   most likely to have been left unsettled.

4. **A sweeper inside Postgres, on pg_cron.**
   Pros: settlement stops depending on any app process being alive, which is the property that
   matters — the books close whether or not anything is deployed. Migration 0017 already schedules
   `app.sweep_expired_field_values` this way, including the fallback notice when pg_cron is absent,
   so there is a pattern to follow rather than a precedent to set.
   Cons: `credit_settle` checks `app.current_org_id()`, and pg_cron has no tenant session, so the
   function has to be split before it can be reused.

## Decision

**Option 4.** `credit_settle` splits into two:

- `app.credit_settle_job(p_job_id, p_org_id, p_consume_id, p_release_id)` — the existing body, with
  the org taken as an argument instead of read from the session. Owner-only; granted to nobody.
- `app.credit_settle(p_job_id, p_consume_id, p_release_id)` — unchanged signature and unchanged
  behaviour for the API: it still resolves the job's org, still refuses a job outside the active
  org, then delegates. `app_api` keeps exactly the grant it has.

A new `app.settle_finished_jobs(p_limit)` then walks jobs whose status is `completed`, `failed` or
`cancelled` and whose `settled_at` is null, oldest first, and settles each one. pg_cron runs it on a
short interval.

Two details that decide whether this works on the data we already have:

- **A job with no reservation must not raise.** `credit_settle` raises `22023` when
  `credit_reserved_for` is 0, which is correct for an API caller settling a specific job — it means
  the caller is confused. For the sweeper it is just a historical row: two of the three jobs above
  carry `credits_reserved = 80` with no `reserve` ledger row, from development before the ledger was
  wired. A sweeper that raises on those would fail on its first row for ever and settle nothing. It
  records them as settled with whatever usage says, and the mismatch is logged rather than retried.
- **Each job settles in its own transaction.** One job that cannot settle must not roll back the
  ones before it in the batch.

## Consequences

**Easier.** `credits_used` becomes a real number, so the job page stops reporting 0 for work that
was charged, and the sentence in `docs/DEMO.md` — "it holds the credits, runs, and gives back
whatever it did not use" — becomes true instead of aspirational. Held reservations return to
balances. The Phase 2 criterion can be checked against the ledger instead of argued about.

**Harder.** Settlement is now eventually consistent: a job is terminal for up to one sweep interval
before its credits move. The job page should say "settling" rather than show a zero, or the fix
trades a wrong number for a stale one. And there is a new thing that can silently stop — a pg_cron
job nobody is watching — so `app.settle_finished_jobs` returns what it did and the admin connectors
page is the place to show the backlog.

**Not covered.** `research_tasks.cost_micros` is still written as 0 by the discovery handler
(migration 0022), so per-task cost remains unavailable. That is a separate fix in the metering path:
what a search cost is known inside `ConnectorHttpClient` and never travels back to the handler.

**Follow-up.** Phase 2's acceptance criteria are re-checked against the ledger after this lands. The
second criterion — 200 unique candidates for Delhi — currently reads 175 on the best run, bounded by
its own credit budget rather than by coverage, and should be re-measured once a run can be funded
honestly.
