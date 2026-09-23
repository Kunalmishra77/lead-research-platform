# ADR-0008: Cancelling a research job that is already queued

- Status: Accepted
- Date: 2026-09-23

## Context
`POST /app/research/:id/cancel` (task 2.6) marks the job cancelled and releases the reservation. But the `research.plan` envelope is already in a Redis stream, and later the planner will fan out many task envelopes. Deleting queued messages is not possible once a consumer has claimed them, and a worker that keeps running after the release would deliver leads the ledger no longer covers: unmetered spend, which CLAUDE.md's budget rule forbids.

## Options considered
1. **Database status only.** The worker reads `research_jobs.status` before each step. One more query per step against the pooled connection, and workers that batch steps can still race the status change.
2. **Redis flag written by the API.** The API sets `research:cancelled:<job_id>` before it releases credits. Workers check a local Redis key (sub-millisecond) as often as they like, including immediately before recording usage.
3. **Cancel message on a control stream.** Needs a consumer per worker and ordering guarantees; heavier than the problem.

## Decision
Option 2, with the status in Postgres as the durable record.
- The API sets `research:cancelled:<job_id>` = `1` (TTL 24 h, longer than the longest run) **before** updating the status and releasing credits.
- Workers must check that key: after claiming a job envelope, before starting each task, and before writing a `usage_events` row. On a raised flag they stop, mark their task `cancelled`, and record no usage.
- The flag is advisory and may expire; `research_jobs.status = 'cancelled'` stays the source of truth for the UI and for reporting.

## Consequences
- A worker can deliver at most the work already in flight at the moment of cancellation. Because `app.credit_settle` books usage incrementally, anything it recorded before stopping is still charged correctly, even after the release.
- Workers gain a small Redis dependency for cancellation, which they already have for the job streams.
- Tasks 2.10 and 2.11 must implement the checks; the executor's tests cover "cancelled mid-run stops and bills only what was delivered".
