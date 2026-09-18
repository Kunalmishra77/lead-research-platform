# PROGRESS.md — build log

Append a new entry at the TOP after every working session. Keep entries short. Claude reads the latest 3 entries at the start of each session.

## Template

```
### YYYY-MM-DD — Phase N — <short title>
- Done:
- Decisions (link ADRs):
- Tests/checks status:
- Open issues / blockers:
- Next step:
```

---

### 2026-09-18 — Phase 1 — Task 1.7 (API part) Supabase Auth + RBAC
- Done:
  - Global `AuthGuard` (jose, remote JWKS; ES256/RS256 only; exact issuer, audience `authenticated`, required `exp`/`iat`/`sub` and UUID `session_id`; anonymous tokens rejected; JWKS outage returns 503) with a `@Public()` opt-out.
  - `TenantGuard` + `@RequirePermission()` (X-Workspace-Id, DB role, session-revocation check, the docs/05 matrix; fails closed without a permission).
  - `GET /app/me` and `POST /app/orgs` via `app.bootstrap_org` (UUID v7 ids, slug generation, distinct error codes). Migration 0005 adds `app.session_is_active` and custom SQLSTATEs LF001-LF003.
  - ESLint blocks direct `drizzle-orm` imports in the API; Drizzle operators come from `@leadforge/db`.
- Decisions (link ADRs): ADR-0002 note on session revocation; docs/05 auth section updated.
- Tests/checks status: api 64 tests. Unit: RBAC matrix, token verifier with local ES256 keys (expired, issuer, audience, foreign key, HS256, anonymous, no exp, JWKS outage). e2e: 401 handling. Live against the dev DB, 10 tests: org create + audit row, /me isolation, 409 slug, unconfirmed email 403, RBAC owner/viewer/non-member/header, revoked session 401; cleanup verified. All packages green. Code-reviewer: CHANGES REQUESTED -> all should-fix items fixed.
- Open issues / blockers: 1.7 checkbox stays open until the server-side auth routes in Next.js exist (built with 1.11). Signup and login need `SUPABASE_PUBLISHABLE_KEY` (Project Settings -> API Keys), which the owner must add to `.env`. `SUPABASE_JWKS_URL` must stay on the same origin as `SUPABASE_URL`.
- Next step: task 1.8 RLS isolation suite (cross-org read/write on every tenant table).

### 2026-09-18 — Phase 1 — Task 1.6 database schema, RLS and tenant helpers
- Done: `db/` package `@leadforge/db`. Drizzle schema in schema `app`: organizations, workspaces, memberships, user_profiles, sources, research_jobs (minimal), credit_ledger, usage_unit_keys; plus partitioned audit_logs and usage_events. Migrations 0000-0004 applied to the dev DB. RLS enabled and forced on every table (partition children too). Context helpers `app.current_org_id()` / `app.current_user_id()`. SECURITY DEFINER functions `bootstrap_org`, `admin_list_orgs`, `admin_list_users`, `is_platform_staff`, `ensure_monthly_partitions` (nightly pg_cron job, 12 months pre-created). `withTenant` / `withUser` with UUID validation and a DB-side membership check. Migrate and seed scripts (11 sources). API `DbModule` exposes Drizzle as `DB`.
- Decisions (link ADRs): ADR-0003 amended (own partition function instead of pg_partman; composite (id, org_id) FKs; billing columns and ledger inserts closed to app_api; bootstrap never re-grants existing tables).
- Tests/checks status: db tests 17/17 against the live dev DB (structural invariants plus tenant context), 2 pass and 15 skip when no DB is configured. Workspace: lint, typecheck, build and format green; api 26, contracts 32, dev-dns 10 tests. Code-reviewer: CHANGES REQUESTED -> the cross-org workspace leak (blocker), partition-grant exposure (blocker) and all should-fix items fixed in migrations 0003/0004.
- Open issues / blockers: RBAC for membership/role changes is API-side (tasks 1.7/1.8 must test it). Credit reservation and grants need definer functions (Phase 2).
- Next step: task 1.7 Supabase Auth (needs the publishable key for the server-side auth calls; JWT verification via JWKS works without it).

### 2026-09-18 — Phase 1 — Task 1.5 API skeleton
- Done: `apps/api` NestJS 11 + Fastify (CJS): Zod-validated env (`NODE_ENV` required), `/health/live` + `/health/ready` (DB/Redis/S3 with real abort on 3 s deadline), RFC 7807 problem+json filter + `AppError`, request ids (UUID v7 or valid incoming `X-Request-Id`, echoed in header, same id in pino logs), nestjs-pino with header redaction + query-string-free request serializer, OpenAPI at `/docs` + `/docs/json` (non-production), global ZodValidationPipe, 1 MB body limit, postgres.js/ioredis/S3 infra modules with shutdown hooks.
- Decisions (link ADRs): build via `nest build` (cleans dist; `rm -rf` is denied in settings); only Fastify `FST_*` errors pass their message through, everything else unknown -> 500; problem `type` base `https://leadforge.dev/problems/<code>`.
- Tests/checks status: api 26 tests (unit: problem mapping, env, logging redaction; e2e via app.inject: health ok/503/timeout-abort, request id, 404/400/413/500 problems, OpenAPI); all packages 68 tests; lint/typecheck/format/build green. Live run against Supabase + local Redis: /health/ready all ok, query secrets not logged. Code-reviewer: CHANGES REQUESTED -> all should-fix items fixed.
- Open issues / blockers: SSE handler (1.10) must set `x-request-id` itself (onSend hook does not run for hijacked replies). Client-supplied `X-Request-Id` is trusted (edge-only trust later).
- Next step: task 1.6 Drizzle schema, migrations, RLS, `withTenant()`.

### 2026-09-18 — Phase 1 — Task 1.3 hosted dev infra (no Docker)
- Done: `infra/setup` (bootstrap.sql + `pnpm infra:bootstrap`: schema `app`, extensions in schema `extensions`, login roles `app_api`/`app_worker`, grants + default privileges, global EXECUTE revoke; `infra:buckets` created private `lf-raw`/`lf-exports`; `infra:check`; `redis:start` portable Redis 8.10.2 for Windows, checksum-pinned, 127.0.0.1), `@leadforge/dev-dns` (DoH for `*.supabase.co`, dev/test only), SETUP.md, `infra/setup/env.template`.
- Decisions (link ADRs): ADR-0002 amended: dev Redis is local (not Redis Cloud); custom roles work through Supavisor (`<role>.<ref>`), so no SET ROLE fallback. Role passwords are set as client-side SCRAM verifiers with a fixed per-role salt: the pooler caches credentials and a new salt breaks logins until its cache refreshes. Tests run on the dev project with rollback (no separate test project) until the owner creates one.
- Tests/checks status: `infra:check` 6/6 PASS live (owner, app_api, app_worker via tx pooler, Redis, S3, JWKS ES256); bootstrap re-run is idempotent; dev-dns 10 tests, contracts 32 tests; lint/typecheck/format green. Code-reviewer: CHANGES REQUESTED -> all blocker/should-fix items fixed.
- Open issues / blockers: `.env` must still be created by the owner from `infra/setup/env.template` (Claude denied); I run with in-memory values. Task 1.6: keep Drizzle's migrations table out of schema `app`. Task 1.8: assert every `app` table has RLS enabled+forced and app_worker cannot execute SECURITY DEFINER functions. `.env.example` is stale (points to template).
- Next step: task 1.5 API skeleton.

### 2026-09-18 — Phase 1 — Task 1.4 contracts package
- Done: `packages/contracts` with JSON Schemas (job-envelope v1, progress-event v1, research-spec v1 skeleton), `scripts/gen.ts` (TS types + schema consts via json-schema-to-typescript; Pydantic v2 models via datamodel-codegen 0.82 with `WireModel` base + `to_wire()`), ajv 2020 validators, shared fixtures, `pnpm contracts:gen` / `contracts:check` (also removes/flags orphaned generated files). Base tsconfig moved to `packages/config/tsconfig/base.json` (Vite does not follow pnpm symlinks for relative `extends`).
- Decisions (link ADRs): envelope budget uses `cost_cap_micros` (integer money) instead of `cost_cap_usd`; progress event carries `org_id` + `trace_id`; envelope if/then requires `org_id` when `research_job_id` is set (TS-only in schema; workers parser must re-check in 1.9); Python strict int/str/bool; `fields` uniqueness enforced TS-side only.
- Tests/checks status: vitest 32 passed (fixtures + Python->TS cross-language round trip), pytest 23 passed / 1 skipped (TS-only if/then rule); typecheck, lint, format, contracts:check green. Code-reviewer: CHANGES REQUESTED -> all blocker/should-fix items addressed.
- Open issues / blockers: CI must install uv + Python 3.12 for the contracts job (task 1.14); `ajvFormats.default` interop may need a fallback if validators are ever bundled for the browser.
- Next step: task 1.3 hosted infra setup (portable Redis, DoH resolver for supabase.co, bootstrap SQL) with in-memory credentials.

### 2026-09-18 — Phase 1 — Task 1.2 monorepo tooling
- Done: pnpm workspace + Turborepo 2.10, `tsconfig.base.json` (strict, noUncheckedIndexedAccess), `packages/config` (ESLint 10 flat configs `base` + type-aware `typescript(dir)`, tsconfig presets node/next/library), Prettier, EditorConfig, `.gitattributes` (LF), Husky pre-commit (lint-staged) + commit-msg (commitlint), `.nvmrc` 24.
- Decisions (link ADRs): ADR-0004 versions; import order via `eslint-plugin-simple-import-sort` (eslint-plugin-import does not support ESLint 10); pnpm 11 auto-added `minimumReleaseAgeExclude: prettier@3.9.8`.
- Tests/checks status: `pnpm lint`, `pnpm typecheck` (no TS packages yet), `pnpm format:check` pass; lint probe catches unsorted imports + unused vars; commitlint rejects "bad msg", accepts "chore: ok".
- Open issues / blockers: Node is 22.18 locally (engines warns, wants 24); owner actions from ADR-0002 still pending.
- Next step: task 1.3 hosted infra setup (needs Redis Cloud URL, test project, DoH, `.env`).

### 2026-09-18 — Phase 1 — Phase started, plan approved
- Done: Phase 1 plan approved; phase set to in progress; task 1.1 ticked (ADR-0002/0003); ADR-0004 pins TS 6.0, NestJS 11 + nestjs-zod, Node 24, asyncpg.
- Decisions (link ADRs): docs/adr/0004-toolchain-version-pins.md; platform staff flag set manually via SQL for now.
- Tests/checks status: n/a (no code yet).
- Open issues / blockers: owner to narrow the `.env.*` deny rule in `.claude/settings.json` (Claude is blocked from self-editing settings) so `.env.example` can be maintained; owner actions from ADR-0002 still pending (Redis Cloud, test project, Node 24, Windows DoH, `.env`).
- Next step: task 1.2 monorepo tooling.

### 2026-09-18 — Phase 0 — Orientation, hosting and auth decisions
- Done: orientation review of all docs; git repo initialised with GitHub remote; ADR-0002 (no Docker: Supabase DB/Auth/Storage + Redis Cloud) and ADR-0003 (DB roles, `app` schema, bootstrap/admin functions, partitioning) accepted; docs, CLAUDE.md and phase-01 updated to match.
- Decisions (link ADRs): docs/adr/0002-hosted-dev-services-and-supabase-auth.md, docs/adr/0003-db-roles-schema-and-partitioning.md. Node 24 LTS target; Python 3.12 via uv.
- Tests/checks status: Supabase dev project verified: PG 17.6, all needed extensions available, SET LOCAL works through transaction pooler, JWKS serves ES256 key.
- Open issues / blockers: ISP DNS-blocks `*.supabase.co` on the dev machine (works over DoH) -> enable Windows DoH; `.env` must be created by the owner (Claude denied); Redis Cloud DB and `leadforge-test` project not yet created; DB password + S3 key were shared in chat -> rotate; Google Places ToS review for the shared graph/exports still to start.
- Next step: `/start-phase 1`.

### (no entries yet) — Phase 0 — Repository initialised with planning files
- Done: planning docs, phase files, Claude Code commands added
- Next step: run FIRST-PROMPT.md in Claude Code to plan Phase 1
