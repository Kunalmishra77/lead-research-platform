# 13 — Testing, QA and Monitoring

## Test pyramid
| Level | TS (web/api) | Python (workers) | Must cover |
| --- | --- | --- | --- |
| Unit | Vitest | pytest | normalizers, matchers, scoring, confidence, credit math, permission checks, mappers |
| Integration | Supertest against Supabase test project (local) / Supabase CLI stack (CI) + Redis | pytest, same targets | RLS isolation, job lifecycle, ledger flows, connector with fixtures, pipeline end-to-end on fixture pages |
| Contract | JSON Schema validation both sides | same | job envelopes, ResearchSpec, progress events |
| E2E | Playwright | - | signup -> research -> results -> list -> export |
| AI evals | - | eval harness | per AI task accuracy (see 07) |

Rules: no live third-party calls in tests (respx / nock / recorded fixtures). Every bug fix adds a regression test.

## Golden datasets
- `services/workers/tests/fixtures/golden/`: ~50 real-world-like company websites saved as HTML (sanitised) with expected extracted fields; ~200 labelled record pairs for entity resolution (same/different).
- Quality report command prints fill rate, precision per field, duplicate rate.

## Phase acceptance checks
Each phase file lists acceptance criteria. `/review-phase <n>` must show evidence (test output, screenshots, metrics) for each.

## Quality gates (MVP)
- Website fill rate >= 70%, duplicate rate < 5%, email extraction precision >= 95% on golden set, email bounce < 8% in beta.
- API p95 < 300 ms for CRUD, first streamed result < 60 s.

## Monitoring metrics (instrument as components are built)
| Metric | Labels | Alert (start) |
| --- | --- | --- |
| crawl_requests_total / crawl_success_ratio | connector, domain_class, status | success < 85% for 30 min |
| access_restricted_total | connector | > 2x baseline |
| job_failures_total, dlq_size | stream, type | > 2% of jobs/hour |
| stream_lag_seconds (oldest pending) | stream | interactive > 600 s |
| stage_duration_seconds | stage | p95 regression > 50% |
| ai_calls_total, ai_schema_failures_total, ai_cost_micros | task, model | failures > 3% |
| cost_per_lead_micros | depth, source | > 1.5x target |
| duplicate_rate, verified_ratio, email_bounce_rate | workspace | bounce > 8% |
| export_failures_total | format | spikes |
| http_request_duration_seconds | route | 5xx > 1%, p95 > 800 ms |

## CI (implemented in task 1.14)
`.github/workflows/ci.yml` runs on pushes to `main` and on pull requests. It has five jobs:
- `js`: format, lint, typecheck, contracts check, build, and unit tests. Live suites skip here because no DB/Redis env is set.
- `web-e2e`: Playwright smoke tests against a production build with a placeholder env.
- `workers`: ruff, `ruff format --check`, strict mypy on `app/`, and pytest, first on fakeredis and then on a real Redis service.
- `integration`: starts a throwaway Supabase CLI stack (DB and Auth only) and a Redis service, runs `infra:bootstrap` and `db:migrate`, then the DB suites (structure and RLS isolation) and the API live suites, including the `system.ping` round trip with the real worker.
- `security`: gitleaks over the full history (reviewed false positives go in `.gitleaksignore` by fingerprint), `pnpm audit --audit-level high`, and pip-audit on the locked worker dependencies.
