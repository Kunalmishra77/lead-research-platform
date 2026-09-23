# ADR-0010: Eval runs are metered by reporting, not by `usage_events`

- Status: Accepted
- Date: 2026-09-23

## Context

CLAUDE.md states without exception that every paid API, LLM or browser call goes through a
metered client that records `usage_events`, and docs/07 repeats it for the AI layer. The eval
runner (`app/ai/evals/runner.py`, task 2.3) makes real, billed model calls — a full `spec_parse`
run is 59 of them — and it deliberately does not go through `AiGateway`.

It cannot, for two reasons that are not negotiable:

- **The response cache would invalidate the result.** An eval scored from cached answers measures
  the cache, not the prompt. The second run of a changed prompt would report the old prompt's
  score.
- **There is no tenant to attribute the spend to.** `usage_events` is a tenant table: it requires
  `org_id`, and `usage_unit_keys` requires a `research_job_id` with a real row behind it. An eval
  belongs to whoever is changing a prompt, not to a customer, and inventing a synthetic org would
  put operator spend inside a tenant's books.

Leaving the calls silently unaccounted was the third option, and the one the first review of task
2.2 correctly refused to let stand elsewhere.

## Options considered

1. **Route evals through the gateway** — satisfies the letter of the rule, breaks the measurement
   (cache) and needs a fake org and job to satisfy the FKs.
2. **A system org and a synthetic research job** — keeps `usage_events` as the single ledger, at
   the cost of a permanent fake tenant that RLS, billing and every per-org report must then learn
   to exclude.
3. **Meter by reporting** — compute the real cost from the provider's own token counts, and put
   it where the people spending it will see it.

## Decision

Option 3. Eval runs are operator tooling, and their spend is accounted for in three places rather
than in `usage_events`:

- the run's own output (`cost=… micros (… per 1k cases, … in / … out)`), from
  `ProviderResult.usage` priced by `app/ai/config.price_for`;
- `meta.cost_micros` inside the recording the run produces;
- the `cost/1k` column in `app/ai/evals/RESULTS.md`, which docs/07 already required.

The exception is scoped to `app/ai/evals/`. Every other path to a model goes through `AiGateway`,
which meters, caps and caches — and the runner's own docstring says why it does not.

## Consequences

- Eval spend does not appear in `usage_events` and is therefore not in any org's usage report.
  That is the intent: it is our cost, not a customer's.
- If an eval is ever run *on behalf of a customer* (a "test this prompt on my data" feature),
  this ADR does not cover it: that path goes through the gateway like everything else.
- `RESULTS.md` carries cost per 1000 cases, so a model or prompt change that makes a task three
  times more expensive is visible in the same table as the score it bought.
- The runner never touches the response cache, so a score always reflects the prompt under test.
