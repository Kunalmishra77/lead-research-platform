# ADR-0002: Hosted dev services (no Docker) and Supabase Auth

- Status: Accepted
- Date: 2026-09-18
- Amends: ADR-0001 (local infra and auth parts only; the TS <-> Python job contract is unchanged)

## Context
The main development machine (Windows 11) cannot run Docker or WSL2, so the planned `infra/docker/compose.dev.yml` stack (Postgres, Redis, MinIO, Mailpit) and Testcontainers-based tests cannot run locally. The owner has chosen Supabase (free plan now, paid later, region `ap-south-1` Mumbai) for the database, auth and storage.

Verified in current Supabase docs (2026-09-18):
- Auth access tokens can be signed with asymmetric keys (ES256/RS256); public keys at `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json`. The HS256 shared secret is "not recommended for production".
- Built-in auth email only delivers to members of the project's team, max 2 messages/hour. Custom SMTP is required for real users.
- Storage speaks the S3 protocol (SigV4, multipart, presigned URLs) once S3 access keys are enabled. No versioning, no SSE.
- Free plan: 2 free projects, 500 MB DB, 1 GB storage, 5 GB egress; inactive projects are paused.
- Redis Cloud free: 30 MB, 30 connections, 100 ops/s, single non-HA DB; may be deleted after 14 days of inactivity.

Verified against the dev project (2026-09-18): PostgreSQL 17.6; extensions vector 0.8.2, postgis 3.3.7, pg_trgm, citext, pgcrypto, pg_cron, pg_partman available; `postgres` role has CREATEROLE and BYPASSRLS (not superuser); `SET LOCAL` works through the transaction pooler; JWKS already serves an ES256 key.

**Indian ISP DNS block:** the developer's ISP resolves `*.supabase.co` to a sinkhole IP (TLS handshake fails), even for queries sent to 1.1.1.1 on port 53. It resolves correctly over DNS-over-HTTPS (Cloudflare IP, 401/403 as expected). The DB pooler host (`*.pooler.supabase.com`) is not affected. Indian end users on similar ISPs would hit the same block.

## Options considered
1. Docker/WSL2 local stack as planned: not possible on this machine.
2. Native Windows installs (Postgres + extensions, Memurai, MinIO exe): pgvector/PostGIS on Windows are painful; setups drift between machines.
3. Hosted services: Supabase (Postgres + Auth + Storage) and Redis Cloud. Nothing heavy runs locally; closer to production; needs internet; free-tier limits.
4. Keep Better Auth, only the DB on Supabase: one fewer vendor dependency, but the owner prefers Supabase Auth.

## Decision
Option 3, with Supabase Auth replacing Better Auth.

| Concern | Choice |
| --- | --- |
| Database | Supabase Postgres. All app tables in schema `app`, which is **not** exposed through the Supabase Data API; `anon`/`authenticated` get no grants on it. RLS on tenant tables as in docs/04 (ADR-0003). |
| Connections | API: transaction pooler `:6543` (prepared statements off). Workers: transaction pooler, async driver with statement cache off. Drizzle migrations: session pooler `:5432`. Tenant context via `SET LOCAL app.org_id` inside each transaction (works with transaction pooling). |
| Auth | Supabase Auth (email + password with email confirmation now; Google OAuth later). **The browser never calls `*.supabase.co` directly** (ISP block): sign-up, login, logout, confirmation callback and token refresh run server-side in Next.js route handlers / server actions using `@supabase/ssr`, with the session in httpOnly cookies on our own domain. Web calls the API with `Authorization: Bearer <access_token>`. The API verifies tokens against the project JWKS (asymmetric keys must be enabled) and never trusts client-sent org IDs. |
| Users, orgs, roles | `auth.users` is Supabase-owned. We own `app.user_profiles` (1:1, holds `is_platform_staff`), `organizations`, `workspaces`, `memberships` with our 5 roles. Org + default workspace are created by an API call after the first verified login (no triggers on the `auth` schema). |
| Auth email | Built-in Supabase email for Phase 1 dev (team members only). Custom SMTP (e.g. Resend) before any outside user signs up. Mailpit dropped. |
| Object storage | Supabase Storage via its S3 endpoint, private buckets `lf-raw`, `lf-exports`. Code uses a generic S3 client, so moving to R2 later is config only. |
| Redis | Dev: portable Redis 8 for Windows (`pnpm redis:start`, checksum-pinned download from github.com/redis-windows, Apache-2.0) on 127.0.0.1. Staging/prod: Redis Cloud or another managed Redis (the free Redis Cloud tier is too small for crawl volume). |
| Local runtime | API, web and workers run natively on Windows. `uvloop` only when not on Windows. The dev machine enables Windows DNS-over-HTTPS (or uses a DoH-resolving DNS) so server-side code can reach `*.supabase.co`. |
| Production reachability | Before beta, put a Supabase custom domain (paid add-on) or our own reverse proxy in front of Auth/Storage so no customer-facing URL depends on `supabase.co`. Signed download URLs for exports are served through our domain. |
| Tests | Local integration tests run against a second Supabase project `leadforge-test` (never dev). CI (GitHub Actions) runs the Supabase CLI local stack + Redis service container per run, so CI never touches hosted data. |

## Consequences
- Amendment 2026-09-18 (task 1.3): custom login roles `app_api`/`app_worker` work through the Supabase pooler (`<role>.<project-ref>` user), so the ADR-0003 `SET ROLE` fallback is not needed. Dev Redis is local (see Redis row). Node/Python resolve `*.supabase.co` over DoH when `DEV_DNS_OVER_HTTPS=true` (`@leadforge/dev-dns`).
- Phase 1 task 1.3 becomes "hosted dev services + setup guide + bucket/role bootstrap scripts" instead of a compose file; 1.7 becomes Supabase Auth; 1.8 uses the test project locally and the Supabase CLI in CI.
- Docs to update after acceptance: CLAUDE.md (stack, commands), docs/01, 02, 04 (auth tables, `app` schema), 05 (auth section), 10 (session/cookie controls), 13 (Testcontainers), phase-01, `.env.example`.
- Development needs internet. Free-plan pausing/deletion after inactivity must be expected; the setup guide covers restoring.
- Free limits (500 MB DB, 1 GB storage, 100 ops/s Redis) are fine for Phase 1-2 but not for Phase 3 crawling; plan paid tiers before Phase 3.
- The Supabase `postgres` role is not a superuser; any extension or role step it can't do must be done in the dashboard and documented.
- Owner actions: ~~enable asymmetric JWT signing keys~~ (already ES256), ~~enable Storage S3 access keys~~ (done), create the `leadforge-test` project, create the Redis Cloud database, enable Windows DNS-over-HTTPS on the dev machine, and rotate the DB password and S3 key that were shared in chat.
- `.claude/settings.json` denies reading `.env.*`, which also blocks editing `.env.example`; the owner maintains `.env` and `.env.example` changes are made via the owner or a settings exception.
