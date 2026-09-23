# CLAUDE.md — LeadForge (codename)

Multi-source lead-generation and deep-research SaaS. A user describes the leads they need in plain language; the system plans research, collects data from legitimate public sources and official APIs, extracts, normalizes, deduplicates, verifies, enriches, scores, and exports the results with a source reference on every value.

This file is loaded automatically in every session. Keep it short. Detailed specs live in `docs/` and `phases/` — read them on demand, not all at once.

## How we work (read first)

1. **Phase-driven build.** Work only on the current phase in `phases/PHASES.md`. Never start a later phase's tasks early.
2. **Start of every session:** read `PROGRESS.md` (last entries), then the current phase file, then only the docs that phase lists under "Read before starting".
3. **Plan before code.** For any task larger than a small fix, write a short plan (files to touch, approach, tests) and wait for approval.
4. **Small, verifiable steps.** One task at a time; run lint, type-check and tests after each; commit with a Conventional Commit message.
5. **End of every session / task:** append to `PROGRESS.md` (what was done, decisions, open issues, next step) and tick checkboxes in the phase file.
6. **Decisions:** anything that changes architecture, schema conventions, or a library choice needs an ADR in `docs/adr/` (copy `0000-template.md`).
7. **Unsure? Ask.** Do not invent product requirements. If a spec is silent, propose options and ask.
8. **Verify versions.** Do not assume library/API versions or signatures from memory — check the installed version, official docs, or the package's changelog before using an API.

## Non-negotiable rules

- **Compliance:** never write code that bypasses CAPTCHAs, logins, paywalls, bot challenges, or robots.txt disallows. On any of these, the crawler stops and marks the target `access_restricted`. Prefer official APIs. See `docs/08-DATA-SOURCES.md`.
- **Provenance:** every stored data value records `source_id`, `source_url`, `observed_at`, `method` (api | crawl | ai | user | provider) and `confidence`. No value without provenance.
- **AI honesty:** AI-generated or inferred values are always flagged `method=ai`. AI must never produce contact values (email/phone) that are not literally present in a source; pattern-derived emails come from deterministic code and are flagged `derived_pattern`.
- **Tenant isolation:** every tenant table has `org_id` and Postgres RLS. Never query tenant tables without the tenant context set. Tenant-private data never enters the global company graph.
- **Secrets:** never hardcode secrets or commit `.env`. OAuth tokens and third-party keys are stored encrypted (envelope encryption). API keys are stored hashed.
- **Budgets:** every research job has a credit budget and an internal cost cap. Every paid API / LLM / browser call goes through a metered client that records `usage_events`.
- **No silent failures:** errors are classified (`transient`, `rate_limited`, `access_restricted`, `parse_failed`, `invalid_input`, `budget_exhausted`) and logged with `trace_id`, `org_id`, `job_id`.

## Stack (see `docs/02-TECH-STACK.md`, `docs/adr/0001-stack-and-job-contract.md`, `0002-hosted-dev-services-and-supabase-auth.md`, `0003-db-roles-schema-and-partitioning.md`)

- `apps/web` — Next.js (App Router), TypeScript, Tailwind, shadcn/ui, TanStack Query/Table/Virtual
- `apps/api` — NestJS, TypeScript, Drizzle ORM (owns ALL DB migrations), Supabase Auth (JWT verified via JWKS), Zod
- `services/workers` — Python 3.12, uv, asyncio, httpx, Playwright, Pydantic v2, SQLAlchemy Core (no migrations in Python)
- Supabase: PostgreSQL 17 (+ pgvector, pg_trgm, PostGIS; app tables in schema `app`), Auth, Storage (S3 API) · Redis 8 (Streams for jobs, cache, rate limits): local portable build in dev, managed in prod
- No Docker on the dev machine: all infra is hosted (ADR-0002). The browser never calls `*.supabase.co` directly (Indian ISP DNS block); auth runs server-side in Next.js.
- Job contract between TS and Python = Redis Streams + JSON envelope defined in `packages/contracts` (JSON Schema). Never call Python from Node directly.

## Repo map

```
apps/web            Next.js frontend
apps/api            NestJS API (REST + SSE), auth, tenants, billing, public API
services/workers    Python: orchestrator, connectors, crawler, extract, normalize, resolve, verify, enrich, score, export, crm
packages/contracts  JSON Schemas: ResearchSpec, job envelopes, field catalogue -> generated TS + Pydantic types
packages/ui         shared React components
db/                 migrations (drizzle), rls policies, seeds
infra/              setup scripts + guide for hosted services, later terraform/k8s
docs/               specs (numbered), adr/
phases/             phase plans with checklists
```

## Commands (fill in exact commands during Phase 1 and keep this list accurate)

```
pnpm install                 # JS deps
pnpm dev                     # builds packages, then web :3000 + api :4000 (turbo); API alone: pnpm --filter @leadforge/api dev (/docs, /health/ready)
pnpm lint && pnpm typecheck  # root files + all TS packages (turbo)
pnpm test                    # TS tests (live DB/Redis suites skip unless DATABASE_URL* + REDIS_URL are set)
pnpm build / build:packages  # everything / only packages/* (infra:* and db:* scripts run build:packages first)
pnpm --filter @leadforge/web build && pnpm --filter @leadforge/web e2e   # Playwright smoke (chromium)
pnpm format / format:check   # prettier (docs/*.md and services/workers are excluded)
pnpm db:generate             # drizzle-kit: SQL from db/schema (partitioned tables + RLS live in custom migrations)
pnpm db:migrate              # apply migrations as owner (DATABASE_URL_MIGRATIONS)
pnpm db:seed                 # idempotent seeds (sources)
pnpm contracts:gen           # regenerate TS + Python types from JSON Schemas
pnpm contracts:check         # fail if generated contracts are stale (CI)
cd services/workers && uv sync && uv run pytest && uv run ruff check . && uv run mypy
cd services/workers && WORKER_POOLS=system,interactive,discovery uv run python -m app.main   # workers; `interactive` answers POST /app/research/parse (ADR-0005), `discovery` plans research jobs and runs them
# REDIS_TEST_URL=redis://127.0.0.1:6379/15 uv run pytest   -> same suite on real Redis (db flushed)
pnpm redis:start             # local Redis (separate terminal, keep running)
pnpm infra:bootstrap         # schema app, extensions, app roles (idempotent)
pnpm infra:buckets           # storage buckets
pnpm infra:check             # all dev services must PASS (setup guide: infra/setup/SETUP.md)
```

## Coding conventions (full list: `docs/12-CODING-STANDARDS.md`)

- TypeScript strict; no `any`. Python: type hints everywhere, mypy strict on `app/`, ruff.
- IDs: UUID v7. Timestamps: `timestamptz`, UTC. Money: integer minor units. Phones: E.164. Countries: ISO-3166 alpha-2.
- Validate all external input with Zod (TS) / Pydantic (Python).
- Every connector implements the connector contract in `docs/08-DATA-SOURCES.md` and ships with recorded fixtures + tests. Tests never hit live third-party APIs.
- Feature folders, not layer folders. Keep files under ~300 lines.
- UI follows `docs/09-UI-UX.md`; every data cell shows its provenance badge (found / derived / AI).

## Docs index (read on demand)

| File | When to read |
| --- | --- |
| docs/00-PRODUCT-BRIEF.md | Always once per phase |
| docs/01-ARCHITECTURE.md | Any backend/infra work |
| docs/02-TECH-STACK.md | Adding a dependency |
| docs/03-FOLDER-STRUCTURE.md | Creating new modules/files |
| docs/04-DATABASE.md | Any schema or query work |
| docs/05-BACKEND-API.md | API endpoints, auth, SSE, public API |
| docs/06-DATA-PIPELINE.md | Crawler, extraction, normalization, resolution, verification, enrichment |
| docs/07-AI-LAYER.md | Anything calling an LLM or embeddings |
| docs/08-DATA-SOURCES.md | Connectors, source policy |
| docs/09-UI-UX.md | Any frontend work |
| docs/10-SECURITY-COMPLIANCE.md | Auth, tokens, exports, deletion, privacy |
| docs/11-BILLING-CREDITS.md | Credits, usage, plans, payments |
| docs/12-CODING-STANDARDS.md | Before first code in a session |
| docs/13-TESTING-QA.md | Writing tests, acceptance checks |
| docs/14-INTEGRATIONS-EXPORTS.md | Exports, Google Sheets, CRMs |

## Slash commands

- `/start-phase <n>` — load context and produce the plan for phase n
- `/next-task` — pick the next unchecked task in the current phase and plan it
- `/finish-task` — run checks, update phase checklist and PROGRESS.md, commit
- `/review-phase <n>` — verify acceptance criteria before closing a phase
- `/new-connector <name>` — scaffold a data-source connector with tests
- `/adr <title>` — create an architecture decision record
