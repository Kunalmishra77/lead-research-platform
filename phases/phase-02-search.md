# Phase 2 — Search & Research Jobs

## Goal
User types a natural-language request, sees an editable ResearchSpec with feasibility and credit estimate, runs it, and gets discovered candidate businesses (from Google Places + SERP) streaming into a basic results table.

## Read before starting
docs/00, 05 (research endpoints, SSE, credits), 06 sections 1-3, 07 (gateway, spec_parse, query_expand), 08 (Places, SERP, contract), 09 (New Research, research job page), 11 (reserve/consume/release)

## Tasks
- [x] 2.1 Finalize `research-spec.schema.json` v1 + industry taxonomy seed (top ~300 categories incl. Indian SMB categories) + geo seed (Indian states/cities with bounding boxes; extendable)
- [x] 2.2 AI gateway v1 in workers: provider adapter(s), task routing config, JSON-schema output, validation + one repair retry, response cache, metering to usage_events
- [x] 2.3 `spec_parse` + `intent_classify` prompts, schemas, 50-case eval set each, eval runner
- [x] 2.4 Parse endpoint path (`POST /app/research/parse`): request -> worker RPC over stream with reply key and 20 s timeout (or ADR alternative) -> spec + feasibility + estimate
- [x] 2.5 Credit estimation service and ledger operations: grant (signup bonus), reserve, consume, release; 402 handling
- [x] 2.6 Research job lifecycle: create search + job (reserve), statuses, cancel, SSE progress, history endpoint
- [x] 2.7 Connector framework: BaseConnector, registry, metered + rate-limited HTTP client, restriction detection hooks, fixtures pattern, `/new-connector` used for the first connector
- [x] 2.8 Google Places connector (Text Search + Details with field masks), geography tiling, caching of place IDs per terms
- [x] 2.9 SERP connector (choose vendor after a small cost/quality test; record in ADR)
- [x] 2.10 Planner v1: intent templates, capability map, `query_expand` task, task DAG in research_tasks, per-task budgets. Capability map reads `registry.providing(field, for_discovery=True)` so non-discovery sources (serp) are never used to *find* companies; a `providing()` hit is not necessarily callable through `search/fetch/map` (serp answers only via `resolve_website`), so the planner must know which shape it is getting — see the 2.9 progress entry.
- [x] 2.11 Discovery executor: run tasks (honour the cancel flag, ADR-0008), upsert candidate companies + locations + field_values with provenance, progress events, stop on budget/limits. Carries four things 2.10 could not finish: the **job-wide spend ceiling in `ConnectorHttpClient`** over the Redis ledger `app/ai/spend.py` uses (`cost_cap_micros` is a total, so no connector can tell what is left); **settling the reservation** of a job that failed or was cancelled, since `credit_settle` is granted to `app_api` only and the worker can mark a job failed but not release its credits; ADR-0008's **third checkpoint in `app/metering/usage.py`**, so a cancel during a paid call is not billed; and re-keying **`ai:spend`**, which is job-wide but now compared against per-task caps. Places raw bodies must never reach `raw_documents` without the ADR-0011 clock.
- [x] 2.8a Value expiry sweeper: delete `field_values` past `observed_at + sources.retention_days` (a new column: retention is not freshness), batched, hourly via pg_cron; unblocks `GOOGLE_PLACES_ENABLED` (ADR-0011)
- [x] 2.12 Web: New Research page (prompt box, examples, SpecChips edit, feasibility badges, depth selector, estimate, run); Research job page (stage progress, counters, credits, streaming basic table); history page; OpenStreetMap attribution ("(c) OpenStreetMap contributors") wherever seeded geography is shown (ODbL, docs/08); Google attribution wherever a Google Places value is shown (ADR-0011)
- [x] 2.13 Admin: jobs list with status and task DAG view; connector health counters

## Acceptance criteria

Verified 2026-10-12. Each line records the evidence, not an opinion.

- [x] **"Restaurants in Delhi with a website" parses to a correct spec in >= 90% of the 50 eval cases for that intent family.** `spec_parse` v4 at medium reasoning: six live runs recorded in `app/ai/evals/RESULTS.md` at 100, 98.3, 98.3, 96.6, 96.6 and 91.5% (mean 96.9%), every one above the bar, and the replay of the recorded baseline scores 59/59 so the cases and the scorer are intact. `sp-036` ("not the big chains") is the one unstable case and is kept rather than tuned away. Not re-run live here: it costs money and the recorded evidence already answers the question.
- [x] **Running it returns >= 200 unique candidates for Delhi with name, address, phone/website where the source provides them, each value with provenance.** Live, 2026-10-12: **225 leads, 225 distinct companies** (246 distinct places between them -- branches of chains sharing one website, which the graph keeps separate on purpose), name 100%, address 90%, phone 86%, email 36%. **13,663 field values, none missing `source_id`, `source_url`, `observed_at`, `method` or `confidence`.** 75/75 tasks, none failed, 6m06s, **0 credits**. This is the criterion the phase was held open on, and it was met at zero cost because ADR-0017 moved discovery to a bulk release on local disk.
- [x] **Credits: reservation shown before run, consumed per delivered candidate, unused released; cancel mid-run releases correctly (ledger test).** `db/test/credits.live.test.ts`, 17 tests against the real database, including the two written for this review: a job cancelled part-way through is charged for the leads it had delivered and gets the rest back, and cancelling twice returns the credits once (a double release would mint credits). The live ledger reconciles exactly: 2,050 granted - 551 consumed = 1,499, with every reservation released.
- [x] **Progress visible live; job survives worker restart.** `progress-stream.test.ts` (13) and `job-live.test.tsx` (5) for the stream; `test_consumer.py` for the restart -- a stale message of a dead consumer is reclaimed, a crash after claiming is taken over, a heartbeat keeps a long job from being reclaimed, and a crash-looping message is dead-lettered. Three real redeploys on 2026-10-12 restarted the workers mid-day and no job was lost (97/97 and 75/75 tasks completed).
- [x] **No test hits live APIs; connectors have fixtures for success, empty, rate-limited, error.** `tests/fixtures/` carries all four for `google_places`, `serp` and `example_source`; every HTTP call in the suite goes through respx. The place-index connectors are file-backed rather than HTTP, so their equivalents are a built SQLite index per test plus `test_health_fails_loudly_when_the_index_is_missing` and `test_health_fails_on_an_empty_index`.

### What is honest about this phase, and is not a criterion

Of the 19 asks `research-spec.schema.json` allows, **6 are applied and 13 are declared
`unsupported` with a reason** (`app/ai/feasibility.py`, held to the schema by
`tests/test_feasibility.py`). That is the state, not a gap in the work: there is no rating in
the place releases, keywords need the site text the crawl collects, and `min_confidence` would
compare against a flat constant until Phase 4 computes confidence per field. What matters is
that the user is told before they spend, and `tests/test_spec_to_leads.py` now runs spec to lead
so a filter cannot claim to work without doing so.

## Out of scope
Website crawling, dedup beyond place_id, verification, scoring, exports.
