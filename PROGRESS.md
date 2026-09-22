# PROGRESS.md — build log

Append a new entry at the TOP after every working session. Keep entries short. Claude reads the latest 3 entries at the start of each session.

## Template

```
### 2026-09-22 — Phase 1 — DONE (all acceptance criteria pass)
- Done: the owner allowed `git push`; main is on GitHub. The first CI runs exposed three issues, all fixed:
  - `astral-sh/setup-uv@v10` does not exist (no moving major tags after v7), so it is pinned to `v10.1.0`.
  - CI never seeded the global sources, so `pnpm db:seed` now runs after migrate.
  - The RLS suite started a "must be denied" insert before awaiting its positive control. On CI's fast local database the rejection arrived first and was reported as unhandled, so the insert is now started only when awaited. The denials themselves were always correct.
  - Run 35704886598: all 5 jobs green (TS checks, web e2e, workers, live Supabase CLI + Redis suites incl. the `system.ping` round trip, security scan and audits).
- Acceptance criteria (final):
  - Fresh clone -> quick start < 15 min: PASS (~13 min).
  - Signup -> org + workspace, login/logout, unverified email blocked: PASS, verified live in the browser 8/8.
  - RLS isolation on every tenant table: PASS (89 tests, locally and in CI).
  - `system.ping` visible live in the UI, crash recovery, DLQ after 5 failures: PASS (live UI plus ping.live in CI).
  - CI green on main, gitleaks clean: PASS.
- Decisions (link ADRs): none new.
- Open issues / blockers carried to later phases: the tech debt list in the 2026-09-18 phase review entry, plus custom SMTP (needed before real users can sign up with any address) and a friendly error page when the API is unreachable.
- Next step: /start-phase 2 (needs AI provider, Google Places and SERP keys; see plan).

### 2026-09-20 — Phase 1 — Task 1.7 verified live in the browser (publishable key received)
- Done: with the owner's Supabase publishable key, I drove the real UI with Playwright against the running dev stack (web + API + Python worker + Redis + hosted Supabase). 8/8 checks pass: anonymous pages redirect to sign-in; sign-up posts to Supabase server-side; an unconfirmed account is refused with "Confirm your email address first"; the confirmation link (`token_hash` on our own domain) starts the session and lands on onboarding; creating the org gives a default workspace and the app shell; `system.ping` runs API -> Redis -> Python worker -> Postgres -> SSE with live progress ending `completed`; sign-out clears the session; the confirmed user signs in again. Screenshots are in the scratch folder.
- Bugs found and fixed (only a live signed-in run could catch these):
  - **App shell crashed every signed-in page**: `AppShell` (server) passed nav items containing Lucide icon components to the client `SidebarNav`. Next.js refuses functions across that boundary, so /dashboard and /dev/ping returned 500. `SidebarNav` now takes `showDeveloper` and builds its own sections; a unit test covers both variants. jsdom tests and the logged-out smoke tests could not see this.
  - The first ping progress row printed the attempt twice ("attempt 1 · attempt 1"); the message no longer repeats what `counts` already carries.
  - Auth failures were silent server-side. `signIn`/`signUp` now log `auth call refused` with the Supabase error code (never the email or password), which is how I identified the SMTP limitation below.
- Decisions (link ADRs): none new.
- Tests/checks status: web vitest 40, lint, typecheck green; workers 36 tests, ruff, mypy green; live browser run 8/8. All test users, orgs, audit rows and job runs were deleted afterwards (project back to 0 users / 0 orgs / 0 job_runs).
- Open issues / blockers: Supabase's built-in email only delivers to project team members, so sign-up with any other address fails until custom SMTP is configured (now in SETUP.md troubleshooting). Email templates still point at supabase.co by default; editing them needs custom SMTP, so the `?code=` fallback covers it for now. A web page shows a 500 when the API is unreachable (no friendly error boundary yet). CI still has not run: the owner has not pushed.
- Next step: owner pushes to GitHub, then close Phase 1 and start Phase 2.

### 2026-09-18 — Phase 1 — Phase review (not closed: 2 criteria wait on the owner)
- Tasks: 15/16 ticked. 1.7 is implemented (API JWKS guard, RBAC, server-side Supabase Auth in web, org + workspace creation) but stays unticked until one live sign-up and sign-in through the web UI succeeds. That needs `SUPABASE_PUBLISHABLE_KEY`.
- Acceptance criteria:
  - Fresh clone -> quick start < 15 min: **PASS** (about 13 min on this machine; task 1.16 entry).
  - Sign-up creates org + workspace, login/logout, unverified email blocked: **API PASS** (orgs.live: org + default workspace + owner, 409 on a taken slug, 403 `auth.email_not_confirmed`; session revocation checked). **Web live flow PENDING** (publishable key).
  - RLS proves org A cannot read/write org B in every tenant table: **PASS** (db 89 tests, including "covers every table in schema app and all partitions force RLS", cross-org FKs, pooled-connection context leak, privileged functions).
  - `system.ping` round trip, worker crash recovery, DLQ after 5 failures: **PASS** against the real worker (ping.live: SSE state -> progress -> done, stale claim after a hard kill, DLQ + `failed`). Seeing it live in the web UI is **PENDING** (needs login, i.e. the publishable key).
  - CI green on main, gitleaks passes: gitleaks **PASS** over full history; CI **PENDING** (the workflow exists and passes actionlint, but it has never run because the owner has not pushed yet).
- Non-negotiables for Phase 1 code:
  - RLS on every tenant table: PASS.
  - Secrets: no secrets in the repo (gitleaks); `.env` ignored.
  - Error taxonomy with trace/org/job ids: PASS, both TS and Python.
  - Provenance, compliance stops, usage metering: N/A this phase (no data values, no crawler, no paid calls yet); the tables and envelope budget fields exist.
- Tech debt / follow-ups:
  - fastify pnpm override: remove when the Nest adapter ships a patched pin.
  - Admin response types are copied by hand in web.
  - No Sentry flush on API shutdown; no source-map upload.
  - The password recovery link lands on /dashboard (no set-password page).
  - The top bar still lacks jobs, notifications and a user menu.
  - The protected layout drops the originally requested path.
  - Supabase Auth per-IP rate limits (all auth calls come from the web server IP).
  - The platform staff flag is set by SQL.
  - esbuild moderate advisory via drizzle-kit (dev only).
  - Local tests use the dev project, not a separate test project.
- Status: Phase 1 stays **in progress** until the owner pushes (CI green) and provides the publishable key (live web sign-up/login + ping in the UI). Then re-run /review-phase 1.

### 2026-09-18 — Phase 1 — Task 1.16 commands + quick start verified from a clean clone
- Done: I cloned the repo into a scratch directory and followed the README quick start as written: `pnpm install` 8m05s, then bootstrap, buckets, `infra:check` (6/6 PASS), migrate and seed (11 sources) 41s, `uv sync` 2m17s. `pnpm dev` came up with API `/health/ready` ok for db, redis and storage, `/docs` 200, `/app/me` 401 without a token, and web `/login` and `/signup` 200 with `/dashboard` redirecting to login. The workers started and consumed the `system` pool.
- Bugs found and fixed:
  - (1) infra:* and db:* scripts failed on a fresh clone because they import built workspace packages. They now run `pnpm build:packages` first (turbo-cached).
  - (2) `pnpm dev` did not build packages. The turbo `dev` task now depends on `^build`.
  - (3) Turbo strict env mode dropped shell env vars for tasks. `globalPassThroughEnv` now lists the app variables, and the web build hashes `SENTRY_DSN_WEB`/`SENTRY_ENVIRONMENT`.
  - (4) The email confirmation flow: Supabase's default template links to supabase.co (blocked by the ISP) and uses PKCE. `/auth/confirm` now also accepts `?code=`, and SETUP.md tells the owner to point the templates at our own domain with `token_hash`.
  - The README quick start is rewritten in the right order, and CLAUDE.md commands are updated. SETUP.md troubleshooting now covers the stale Turbopack cache (every page 404 after a hard kill) and env loading.
- Decisions (link ADRs): none new. ADR-0002 (no browser calls to supabase.co) motivates the email template change.
- Tests/checks status: web vitest 39 (new confirm-route tests), lint, typecheck and format green.
- Open issues / blockers: I could not do a live sign-up or sign-in because the owner has not provided `SUPABASE_PUBLISHABLE_KEY`. The confirm email template change is a manual dashboard step. Setup time on this machine was about 13 min (install dominated by copying the pnpm store across drives), which is inside the 15-minute acceptance target but close.
- Next step: /review-phase 1.

### 2026-09-18 — Phase 1 — Task 1.15 Sentry wiring
- Done: all three services report errors to Sentry, each switched on only by its own DSN (`SENTRY_DSN_WEB`, `SENTRY_DSN_API`, `SENTRY_DSN_WORKERS`; empty means off), plus `SENTRY_ENVIRONMENT` and `SENTRY_TRACES_SAMPLE_RATE` (default 0, which means tracing is off).
  - API (`@sentry/nestjs` 10.75): Sentry is initialised in `main.ts`. The problem-details filter reports unexpected 5xx errors, except classified `transient`/`rate_limited` outages, which are only logged.
  - Web (`@sentry/nextjs` 10.75): the server side initialises in `instrumentation.ts`, and `onRequestError` covers RSC, route handler and server action errors. The browser side (`instrumentation-client.ts`) loads the SDK only when the DSN was set at build time.
  - Workers (`sentry-sdk` 2.69): errors are reported when a job fails for good (not access_restricted or budget_exhausted). Consumer-loop and retry-promotion errors are rate-limited to one report per minute per error type.
  - New package `@leadforge/observability` holds the shared TS scrubber. It keeps an allowlist of headers; strips query and fragment from `request.url`, `contexts.*` URLs/paths (e.g. `nextjs.request_path`), the referer and breadcrumb URLs; drops console breadcrumbs, `extra`, cookies, bodies and user fields other than the id; and redacts exception text (Drizzle `params:`, Postgres `Key (...)=(...)`, emails, tokens). `tracePropagationTargets: []` means no trace headers on outgoing requests.
  - The Python scrubber does the same, with the stdlib LoggingIntegration turned off and `trace_propagation_targets=[]`. The shared `redact()` now also removes SQLAlchemy `[parameters: ...]` and Postgres key values, which also protects logs, the DLQ and progress events.
  - Also fixed: the web app now reads its allowlisted keys from the repo-root `.env`. Before this, `pnpm dev` could not see that file. DB and storage credentials never enter the web process.
- Decisions (link ADRs): each service has its own DSN (separate Sentry projects). No source-map upload yet (`@sentry/cli` build script denied, no `withSentryConfig`). Only manual capture is used in the API, so init in `main.ts` (after imports) is enough. SETUP.md has step-by-step Sentry account instructions for the owner.
- Tests/checks status: observability 8 tests (every scrub path, no PII in the serialized event); API 107 tests incl. the live suites (`shouldReport`, off-by-default, env validation); web vitest 36, Playwright 7 and build pass (no Sentry code in client chunks when the DSN is unset); workers 35 tests on fakeredis and real Redis (redaction, breadcrumb/logentry scrubbing, rate limit, consumer reports only the final failure, not retries); repo-wide lint, typecheck, test and format are green. Code-reviewer: CHANGES REQUESTED (tokens in URL/context, breadcrumbs, exception text, logentry) -> all fixed; re-review APPROVE (nits fixed: `code` redaction only as `code=`, browser environment defaults to NODE_ENV).
- Open issues / blockers: the owner needs to create the Sentry projects and add the DSNs (optional). Browser stack traces stay minified until source maps are uploaded in the deploy pipeline. No explicit `Sentry.close()` flush on API shutdown yet. A production web build must set `SENTRY_DSN_WEB` explicitly, because it is inlined at build time.
- Next step: task 1.16 commands + README quick start.

### 2026-09-18 — Phase 1 — Task 1.14 CI
- Done: `.github/workflows/ci.yml` with five jobs. `js`: format, lint, typecheck, contracts check, build, unit tests. `web-e2e`: Playwright. `workers`: ruff, format check, mypy, pytest on fakeredis and on real Redis. `integration`: Supabase CLI 2.117.0 local stack (DB + Auth) plus a Redis service, bootstrap and migrate, then the DB RLS suites and the API live suites including the `system.ping` round trip with the real worker. `security`: gitleaks 8.30.1 (checksum-verified) over full history, `pnpm audit --audit-level high`, pip-audit. `.gitleaksignore` holds the one reviewed false positive (the e2e placeholder key). The workflow passes actionlint 1.7.12.
- Decisions (link ADRs): ADR-0004 amendment. A pnpm override forces fastify 5.12.5 (fixes GHSA-w2qp-rph6-63g4 and GHSA-3m5p-2c4r-xxw2). `TRUST_PROXY` now accepts only proxy IPs/CIDRs (hop counts are spoofable and were removed in fastify 5.12). The CI mypy step uses the pyproject scope (strict on `app/`).
- Tests/checks status: locally, gitleaks over history finds nothing beyond the ignored placeholder, pnpm audit high passes (1 moderate: esbuild via drizzle-kit, dev-only), pip-audit is clean, API 104 tests pass on fastify 5.12.5, and ruff, format and mypy are clean.
- Open issues / blockers: the workflow has not run on GitHub yet. The owner must `git push -u origin main` (Claude is not allowed to push). The Supabase CLI stack cannot be tried locally (no Docker), so the first run may need small fixes, for example in bootstrap.sql against the local `postgres` role. docs/13 has a CI section.
- Next step: task 1.15 Sentry wiring.

### 2026-09-18 — Phase 1 — Task 1.12 admin shell
- Done: API `admin` module: `GET /admin/orgs` and `GET /admin/users` (Zod-validated `limit`/`cursor`, fetches `limit + 1` so there is no empty last page, Zod-validated DB rows). Migration 0011 recreates `app.admin_list_*` with `p_ip`/`p_user_agent`, so admin audit rows carry the request origin; the old overloads were dropped. Shared `common/db/pg-error.ts` and `common/db/session.ts` (`assertSessionActive`, now also used by TenantGuard). Web `/admin` area (layout 404s for non-staff; orgs/users tables with cursor paging; the fetchers map API 403 to 404 and 401 to /login, because pages render in parallel with the layout check). `@leadforge/db` re-exports the `SQL` type.
- Decisions (link ADRs): ADR-0003 (admin functions audit and enforce staff). Pagination param is `cursor` per docs/05. `admin.forbidden` is classified `access_restricted`. docs/05 has an "Implemented (task 1.12)" note.
- Tests/checks status: API 104 passed incl. admin.live (staff paging + audit with IP, non-staff 403 with no admin audit, revoked/anonymous 401, query validation) and admin.mapping unit tests; DB 89 passed (RLS suite against new signatures); web vitest 36, Playwright 7 (anonymous /admin -> login); lint, typecheck, build green. Code-reviewer: APPROVE -> should-fix 1-5 and nits 6, 7, 9, 10 addressed.
- Open issues / blockers: web types for admin responses are hand-copied from the API DTOs (shared contract later). A signed-in non-staff user getting a 404 on /admin is not e2e-tested yet (needs live Supabase auth; publishable key pending). Platform staff flag is still set manually via SQL.
- Next step: task 1.14 CI.

### 2026-09-18 — Phase 1 — Task 1.11 web skeleton
- Done: `apps/web` on Next.js 16.3 (App Router, `proxy.ts`), Tailwind v4 theme tokens from docs/09, shadcn-style Button/Input/Card, app shell (sidebar in docs/09 order with later-phase items shown as "Soon", credits pill + workspace switcher at the sidebar bottom, top bar with search placeholder, Admin link for platform staff, sign-out). Login/signup/check-email pages and `/auth/confirm` (token_hash) run Supabase Auth server-side only (@supabase/ssr); the proxy gates pages with `getClaims()`. Onboarding creates org + default workspace via the API. `/api/app/*` is a same-origin streaming proxy to the API (bearer token + `x-workspace-id` added server-side, Origin check on writes, `/app/` path confinement, 1 MB streamed body cap, `redirect: manual`, 502 problem+json on upstream failure, SSE `no-cache, no-transform`, `Last-Event-ID` forwarded). Dev ping page shows live SSE progress (hidden in production).
- Decisions (link ADRs): ADR-0002 (browser never calls supabase.co). Workspace switching is a POST server action; `GET /workspace/select` only sets an initial default and never replaces a valid choice (no cross-site switching). `lf_ws` cookie flags come from one helper (`secure` follows APP_URL); cleared on sign-out. Open-redirect guard `safeNextPath` resolves against APP_URL and rejects control chars/backslashes.
- Tests/checks status: vitest 31 passed (redirect guard, proxy path/headers/origin/body cap, cookie flags, workspace action, sign-out clears cookie, shell render); Playwright 6 passed against a production build with placeholder env (no live Supabase); lint, typecheck, build green. Code-reviewer: CHANGES REQUESTED (open redirect via `/\` and tab, `..` path escape in proxy) -> all blockers and should-fix items fixed.
- Open issues / blockers: live login needs `SUPABASE_PUBLISHABLE_KEY` from the owner (so 1.7 web part stays unticked). Supabase Auth rate limits are per client IP and all auth calls now come from the web server IP: check limits / forwarding before launch (docs/10). `recovery` links land on /dashboard (no set-password page yet); PKCE `code` flow not handled, so email templates must use `token_hash`. Top bar still lacks running-jobs indicator, notifications and user menu (later phases). Protected-layout redirect drops the originally requested path.
- Next step: task 1.12 admin shell.

### YYYY-MM-DD — Phase N — <short title>
- Done:
- Decisions (link ADRs):
- Tests/checks status:
- Open issues / blockers:
- Next step:
```

---

### 2026-09-18 — Phase 1 — Task 1.10 system.ping round trip
- Done:
  - `app.job_runs` (migrations 0008-0010: RLS, composite FK, worker column-only UPDATE, narrow API compensation grant).
  - API `JobPublisher` (validates the envelope against the contract before XADD) and dev-only `POST/GET /app/dev/ping-job[/:id[/events]]`. Server-generated trace_id; the row is marked failed if publishing fails.
  - `ProgressHub` (one multiplexed subscriber, per-user and global stream caps) and `streamProgress` SSE (state -> progress -> done with buffering, heartbeat state poll, disconnect cleanup, max duration).
  - Workers: `system.ping` handler, `JobRunsRepo` (never regresses finished rows), registry failure hooks (DLQ, pause, crash-loop), configurable retry base delay and reclaim interval.
- Decisions (link ADRs): `paused` counts as terminal for SSE; `JOBS_SYSTEM_POOL` and `PROGRESS_STREAM_MAX_MS` are configurable (tests use a per-run pool).
- Tests/checks status: acceptance proven live across languages (`apps/api/test/ping.live.test.ts` spawns the real Python worker):
  - SSE round trip.
  - SIGKILL mid-job, restart, completes on the same attempt (stale claim).
  - 5 failures -> DLQ, and the row is failed.

  Also: api unit tests for hub/stream/guard/publisher; workers 25 tests; db 89 live incl. job_runs isolation. All packages lint/typecheck/build/format green. Code-reviewer: CHANGES REQUESTED -> connection-per-client, ordering, orphan rows, trace id, row regression and pause ordering all fixed.
- Open issues / blockers: no Last-Event-ID resume (reconnect gets `state` first instead). Between retries the row stays `running`.
- Next step: task 1.11 web skeleton (Next.js, Supabase server-side auth, app shell, dev ping page with SSE via server proxy).

### 2026-09-18 — Phase 1 — Task 1.9 Python workers skeleton
- Done: `services/workers` (uv, Python 3.12):
  - pydantic-settings config and structlog JSON logs with secret masking.
  - OTel tracing, active only when an endpoint is configured.
  - Async SQLAlchemy Core engine (asyncpg, both statement caches off for the transaction pooler) and `tenant_transaction`.
  - aioboto3 S3 client and dev-only DoH `getaddrinfo` patch.
  - Job framework: envelope parsing (re-checks the tenant if/then rule), handler registry with stages, idempotency with a message-id ownership token and heartbeat.
  - Consumer: groups, XAUTOCLAIM reclaim with cursor, heartbeat via `XCLAIM JUSTID`, delayed retries (one ZSET per stream, Lua promotion, ZADD+XACK in one MULTI), DLQ for terminal/exhausted/crash-loop jobs, `budget_exhausted` pause, failed/paused progress events, error-text redaction, NOGROUP recovery.
  - `python -m app.main` with graceful shutdown (Windows-safe).
- Decisions (link ADRs): mypy strict on `app/` (docs/12); ruff on everything. `JOB_MAX_ATTEMPTS` is capped at 5 (schema limit). Busy duplicates of a running job are acked (the owning message is recovered by reclaim). Budget pre-checks and usage recording will live in the metered clients (Phase 2). The contracts generator now emits `py.typed`.
- Tests/checks status: 20 pytest cases, green on fakeredis and on real Redis 8.10 (`REDIS_TEST_URL`). They cover success, duplicates (sequential and concurrent), retry with next attempt, DLQ after 5, access_restricted, budget pause, failed progress with trace, invalid envelopes, reclaim after crash (with and without claim), crash-loop DLQ, heartbeat vs. rival reclaim, Retry-After, and redaction. ruff and mypy clean. Live smoke: `app_worker` DB context via the pooler, and the worker dead-letters unknown job types. Code-reviewer: CHANGES REQUESTED (blocker: stale claim leading to a wrong DLQ) -> fixed with ownership token + heartbeat; all should-fix items fixed.
- Open issues / blockers: no graceful drain deadline yet (in-flight jobs are cancelled on stop and recovered by reclaim). The DoH patch is dev-only.
- Next step: task 1.10 `system.ping` round trip (API publish -> worker -> DB row -> progress -> SSE).

### 2026-09-18 — Phase 1 — Task 1.13 audit log service
- Done:
  - `AuditService`: tenant events are written inside the caller's `withTenant` transaction. Org-less events go through `app.record_platform_audit` (allowlist: `auth.session_started`, `auth.signed_out`).
  - Login audit: `SessionAuditService` records `auth.session_started` on the first request of each Supabase session. It uses a Redis SET NX marker (30 d), runs detached from the request, and skips (with a log) when Redis isn't ready.
  - Org creation is audited by `bootstrap_org`.
  - Role change: `GET /app/members` (directory via `app.workspace_members`) and `PATCH /app/members/:userId` (`team.manage`). Owner-only rules and the last-owner rule are checked against the actor's role read under `FOR UPDATE`; the audit row is written in the same transaction.
  - Redis connects at startup; `TRUST_PROXY` (hop count or CIDRs, never `true`) makes audit IPs real behind a load balancer.
  - Migrations 0006/0007.
- Decisions (link ADRs): admins can change non-owner roles, including other admins and themselves; owner role changes are owner-only.
- Tests/checks status: api 79 tests (SessionAuditService unit, TRUST_PROXY config, 11 live members/audit tests incl. stale-role and SQL-level directory checks). All packages green; sweep clean. Code-reviewer: CHANGES REQUESTED -> all should-fix items fixed.
- Open issues / blockers: `auth.signed_out` is allowlisted but only emitted once the web sign-out route exists (1.11).
- Next step: task 1.9 Python workers skeleton.

### 2026-09-18 — Phase 1 — Task 1.8 RLS isolation suite
- Done: `db/test/rls.live.test.ts`, 66 live cases as `app_api` and `app_worker`:
  - SELECT on every tenant table returns org A rows only.
  - INSERT for org B fails with the exact expected error: RLS where the role holds INSERT (always with a positive control on its own org), `permission denied` otherwise.
  - UPDATE WITH CHECK: own rows cannot be moved into org B.
  - UPDATE/DELETE never reach org B rows, and org B data is verified intact afterwards.
  - Every partition child is denied directly.
  - No context means no rows, and the context does not leak on a pooled connection.
  - Composite-FK side channels are blocked.
  - Pre-org user view is limited to own memberships; a two-org member sees org names only, no tenant data.
  - `withTenant` refuses foreign orgs.
  - `user_profiles` self-only, and the staff flag cannot be self-granted.
  - Audit actor cannot be forged.
  - Admin/bootstrap function guards hold, and staff listing is audited.
  - `sources` is read-only for the API.
  - Coverage check: every `app` table must be listed in the suite and must force RLS.
  - Added `pnpm db:sweep-test-data`.
- Decisions (link ADRs): ADR-0003 trust-boundary note (RLS guards against context bugs; the API role stays trusted to set context).
- Tests/checks status: db 83/83 live. Code-reviewer: CHANGES REQUESTED (missing WITH CHECK coverage, no positive controls) -> all fixed; sweep found no leftovers.
- Open issues / blockers: CI (1.14) must provide a database for these live suites (Supabase CLI stack); otherwise they skip.
- Next step: task 1.13 audit log service (login, role change), then 1.9 workers.

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
