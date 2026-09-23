# Phase 2 — Search & Research Jobs

## Goal
User types a natural-language request, sees an editable ResearchSpec with feasibility and credit estimate, runs it, and gets discovered candidate businesses (from Google Places + SERP) streaming into a basic results table.

## Read before starting
docs/00, 05 (research endpoints, SSE, credits), 06 sections 1-3, 07 (gateway, spec_parse, query_expand), 08 (Places, SERP, contract), 09 (New Research, research job page), 11 (reserve/consume/release)

## Tasks
- [x] 2.1 Finalize `research-spec.schema.json` v1 + industry taxonomy seed (top ~300 categories incl. Indian SMB categories) + geo seed (Indian states/cities with bounding boxes; extendable)
- [ ] 2.2 AI gateway v1 in workers: provider adapter(s), task routing config, JSON-schema output, validation + one repair retry, response cache, metering to usage_events
- [ ] 2.3 `spec_parse` + `intent_classify` prompts, schemas, 50-case eval set each, eval runner
- [ ] 2.4 Parse endpoint path (`POST /app/research/parse`): request -> worker RPC over stream with reply key and 20 s timeout (or ADR alternative) -> spec + feasibility + estimate
- [x] 2.5 Credit estimation service and ledger operations: grant (signup bonus), reserve, consume, release; 402 handling
- [ ] 2.6 Research job lifecycle: create search + job (reserve), statuses, cancel, SSE progress, history endpoint
- [ ] 2.7 Connector framework: BaseConnector, registry, metered + rate-limited HTTP client, restriction detection hooks, fixtures pattern, `/new-connector` used for the first connector
- [ ] 2.8 Google Places connector (Text Search + Details with field masks), geography tiling, caching of place IDs per terms
- [ ] 2.9 SERP connector (choose vendor after a small cost/quality test; record in ADR)
- [ ] 2.10 Planner v1: intent templates, capability map, `query_expand` task, task DAG in research_tasks, per-task budgets
- [ ] 2.11 Discovery executor: run tasks, upsert candidate companies + locations + field_values with provenance, progress events, stop on budget/limits
- [ ] 2.12 Web: New Research page (prompt box, examples, SpecChips edit, feasibility badges, depth selector, estimate, run); Research job page (stage progress, counters, credits, streaming basic table); history page; OpenStreetMap attribution ("(c) OpenStreetMap contributors") wherever seeded geography is shown (ODbL, docs/08)
- [ ] 2.13 Admin: jobs list with status and task DAG view; connector health counters

## Acceptance criteria
- "Restaurants in Delhi with a website" parses to a correct spec in >= 90% of the 50 eval cases for that intent family.
- Running it returns >= 200 unique candidates for Delhi with name, address, phone/website where Places provides them, each value with provenance.
- Credits: reservation shown before run, consumed per delivered candidate, unused released; cancel mid-run releases correctly (ledger test).
- Progress visible live; job survives worker restart.
- No test hits live APIs; connectors have fixtures for success, empty, rate-limited, error.

## Out of scope
Website crawling, dedup beyond place_id, verification, scoring, exports.
