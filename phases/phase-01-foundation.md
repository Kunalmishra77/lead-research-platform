# Phase 1 — Foundation

## Goal
A running monorepo with local infra, database with RLS, authentication with organizations/workspaces, a TS->Python job round-trip, observability basics, and an admin shell. No product features yet.

## Read before starting
CLAUDE.md, docs/00, 01, 02, 03, 04 (conventions + tenant tables), 05 (auth/RBAC), 10 (Phase 1 rows), 12, 13, adr/0001

## Tasks
- [ ] 1.1 Decide auth integration approach and API framework confirmation (ADR-0002 if deviating from ADR-0001)
- [ ] 1.2 Monorepo: pnpm workspaces, Turborepo, tsconfig base, ESLint/Prettier, Husky + lint-staged, commitlint
- [ ] 1.3 `infra/docker/compose.dev.yml`: Postgres 16 with pgvector + postgis + pg_trgm, Redis 7, MinIO (create buckets), Mailpit; healthchecks; `postgres-init.sql` creates roles `app_api`, `app_worker`
- [ ] 1.4 `packages/contracts`: JSON Schemas for job-envelope, progress-event, research-spec (v1 skeleton); generator script producing TS types and Pydantic models; CI check that generated code is up to date
- [ ] 1.5 `apps/api` NestJS (Fastify) skeleton: config module (Zod-validated env), health endpoints (`/health/live`, `/health/ready` checks DB/Redis/S3), problem+json exception filter, request ID + pino logger, OpenAPI generation
- [ ] 1.6 Drizzle setup in `db/`: schema for organizations, workspaces, memberships, auth tables, audit_logs, sources (seed), research_jobs (minimal), usage_events, credit_ledger; migrations; RLS policies as SQL migrations; `withTenant()` helper
- [ ] 1.7 Better Auth: email/password + verification (Mailpit), Google OAuth, sessions, organizations; create org + default workspace on signup; RBAC guard + permission decorator
- [ ] 1.8 RLS isolation integration test across all tenant tables created so far (Testcontainers)
- [ ] 1.9 `services/workers` Python skeleton with uv: config (pydantic-settings), structured logging, OTel setup, async DB engine, S3 client, Redis Streams consumer framework (consumer groups, XAUTOCLAIM for stale messages, retry with delayed ZSET, DLQ, idempotency via Redis SET NX), progress publisher, error taxonomy
- [ ] 1.10 Round-trip demo job `system.ping`: API endpoint `POST /app/dev/ping-job` publishes envelope -> worker processes -> updates DB row -> publishes progress -> API SSE endpoint streams it
- [ ] 1.11 `apps/web` Next.js skeleton: Tailwind, shadcn/ui init, theme tokens from docs/09, app shell (sidebar, top bar, credits pill placeholder), login/signup/verify pages wired to Better Auth, protected layout, workspace switcher, dev ping-job page showing SSE progress
- [ ] 1.12 Admin shell: `/admin` route group restricted to platform staff flag; users and orgs list (read-only)
- [ ] 1.13 Audit log service used for login, org creation, role change
- [ ] 1.14 CI (GitHub Actions): TS lint/typecheck/test, Python ruff/mypy/pytest, contracts check, build; gitleaks; dependency audit
- [ ] 1.15 Sentry wiring (web, api, workers) behind env flag
- [ ] 1.16 Update CLAUDE.md Commands section with exact working commands; README quick start verified from clean clone

## Deliverables
Monorepo, compose stack, auth flows, tenant-safe DB, job framework, SSE demo, CI green.

## Acceptance criteria
- Fresh clone -> README quick start works in < 15 minutes on a new machine.
- Signup (email + Google) creates org + workspace; login/logout; unverified email blocked from app.
- RLS test proves org A cannot read/write org B rows in every tenant table.
- `system.ping` job round-trip visible live in the web UI; killing the worker mid-job and restarting completes it (stale claim works); a job that fails 5 times lands in DLQ.
- CI green on main; no secrets in repo (gitleaks passes).

## Out of scope
Research features, connectors, billing payments, public API.
