# 05 — Backend API (apps/api)

## Principles
- REST, JSON, OpenAPI generated from Zod. Internal app API under `/app/*`, public API under `/v1/*` (same services, different auth + rate limits).
- Every request: auth -> resolve org/workspace -> RBAC check -> open transaction with `SET LOCAL app.org_id` -> handler -> audit (for sensitive actions).
- Long work = create job + publish envelope + return `202 { job_id }`. Clients follow progress via SSE or polling.
- Errors: RFC 7807 `application/problem+json` with `type, title, status, code, detail, request_id`.
- Pagination: cursor (`?limit=50&cursor=...`), max 1000 (API) / 200 (app).
- Idempotency: `Idempotency-Key` header honoured on POST (stored 24h in Redis).

## Auth and RBAC
- Supabase Auth (ADR-0002): email+password with confirmation; Google OAuth later. All Supabase Auth calls happen server-side in Next.js (browser never calls supabase.co); session in httpOnly cookies on our domain. The API receives `Authorization: Bearer <access_token>` and verifies it against the project JWKS. Orgs, workspaces, memberships and roles are our own tables; `POST /app/orgs` bootstraps org + default workspace through `app.bootstrap_org`.
- Roles: owner, admin, manager, member, viewer (per workspace membership; the role is always read from the DB, never from the token).
- Implemented (task 1.7):
  - A global `AuthGuard` verifies ES256/RS256 access tokens against the project JWKS: exact issuer `${SUPABASE_URL}/auth/v1`, audience `authenticated`, and `exp`/`iat`/`sub` plus a UUID `session_id` required. Anonymous tokens are rejected. A JWKS outage returns 503 `auth.unavailable`, not 401. `@Public()` opts a route out.
  - Workspace-scoped routes use `@RequirePermission(<permission>)`: send `X-Workspace-Id`. The guard checks the session is still active (`app.session_is_active`: not signed out or expired, user not banned or deleted) and the caller's membership. Non-member and unknown workspaces return the same 403 `tenant.forbidden`; a missing permission returns 403 `rbac.forbidden`.
  - Error codes: `auth.missing_token` / `auth.invalid_token` / `auth.session_revoked` (401, `WWW-Authenticate: Bearer[ error="invalid_token"]`), `auth.email_not_confirmed`, `auth.user_blocked`, `org.slug_taken` (409), `org.limit_reached` (422).

| Permission | owner | admin | manager | member | viewer |
| --- | --- | --- | --- | --- | --- |
| run research / spend credits | Y | Y | Y | Y (within user cap) | N |
| view contacts | Y | Y | Y | Y | Y |
| export | Y | Y | Y | Y (row cap) | N |
| manage lists, tags, notes | Y | Y | Y | Y | N |
| manage integrations, API keys | Y | Y | N | N | N |
| billing | Y | Y | N | N | N |
| team management | Y | Y | N | N | N |
| workspace settings, scoring models | Y | Y | Y | N | N |

- Public API keys: `lf_live_<prefix>_<secret>`; store sha256(secret) + prefix; scopes: `research:write, research:read, leads:read, leads:write, lists:write, exports:write, enrich:write, usage:read`.

## App endpoints (MVP-first; phase noted)
| Method | Path | Purpose | Phase |
| --- | --- | --- | --- |
| GET | /app/me | Current user, memberships, active workspace | 1 |
| POST | /app/orgs | Create org + default workspace | 1 |
| POST | /app/workspaces/:id/invites | Invite member | 1 |
| POST | /app/research/parse | NL text -> ResearchSpec + feasibility + credit estimate (sync call to AI gateway via workers RPC-over-stream with 20 s timeout, or API-side LLM call through a shared gateway client; decide in ADR) | 2 |
| POST | /app/research | Create search + research job (reserve credits) | 2 |
| GET | /app/research/:id | Job status, progress, credits | 2 |
| GET | /app/research/:id/events | SSE progress stream | 2 |
| POST | /app/research/:id/cancel | Cancel, release credits | 2 |
| GET | /app/research/:id/results | Paginated leads for job | 3 |
| GET | /app/research | Research history | 2 |
| GET | /app/leads | Search/filter workspace leads | 4 |
| GET/PATCH | /app/leads/:id | Detail / status, assignee, overrides, custom fields | 4 |
| POST | /app/leads/bulk | Bulk tag, status, assign, add to list, enrich, verify | 4 |
| GET | /app/companies/:id | Company with best values + field provenance | 4 |
| GET | /app/companies/:id/provenance?field= | All field_values for a field | 4 |
| POST | /app/companies/:id/rerun | Re-research one company | 5 |
| GET | /app/people/:id | Person detail | 5 |
| CRUD | /app/lists, /app/lists/:id/items | Lists | 4 |
| CRUD | /app/tags, /app/leads/:id/notes | Tags, notes | 4 |
| CRUD | /app/scoring-models | Scoring models | 6 |
| POST | /app/match-feedback | Merge / not-same feedback | 4 |
| POST | /app/exports | Start export | 7 |
| GET | /app/exports/:id | Status + signed URL | 7 |
| GET | /app/integrations/google/connect | OAuth start | 7 |
| GET | /app/integrations/google/callback | OAuth callback | 7 |
| GET | /app/billing/summary | Plan, balance, usage by meter | 7 |
| POST | /app/billing/checkout | Razorpay/Stripe checkout | 7 |
| POST | /webhooks/razorpay, /webhooks/stripe | Payment webhooks (signature verified) | 7 |
| CRUD | /app/saved-searches | Schedules | 8 |
| CRUD | /app/api-keys, /app/webhooks | Keys and outgoing webhooks | 8 |
| CRUD | /app/integrations/crm/:provider | CRM connect, mapping, push | 9 |
| POST | /privacy/requests | Public DSR form | 7 |
| * | /admin/* | Admin panel API (platform staff only) | 1+ |

- Implemented (task 1.12): `GET /admin/orgs` and `GET /admin/users` (`?limit=1..200&cursor=<nextCursor>`, response `{ items, nextCursor }`). Both call the SECURITY DEFINER functions `app.admin_list_orgs` / `app.admin_list_users` after an `app.session_is_active` check. The functions enforce `user_profiles.is_platform_staff` and write an `admin.*.listed` audit row with IP and user agent in the same transaction (migration 0011). Non-staff get 403 `admin.forbidden` and no audit row. Users are ordered by id (keyset), not by signup time, because `auth.users` ids are random. The web `/admin` area renders these lists server-side and returns 404 to non-staff. The browser proxy only reaches `/app/*`, never `/admin/*`.

- Implemented (task 2.6): `POST /app/research` validates the ResearchSpec (packages/contracts) and the industry slugs against `app.industries`, creates the search + job and reserves credits **in one transaction** (402 `credits.insufficient` when the balance is short; nothing is written), then publishes a `research.plan` envelope to `jobs:<JOBS_DISCOVERY_POOL>` carrying the job's budget and the API request id as `trace_id`. The org's credit lock is taken before the inserts, so two runs starting at once queue instead of deadlocking (a serialization failure still maps to 503 `research.busy`). `Idempotency-Key` is honoured for 24 h per org and route: a retry replays the first answer instead of reserving again. `GET /app/research` (cursor paging by job id, newest first), `GET /app/research/:id` (status, spec, credits), `GET /app/research/:id/events` (SSE) and `POST /app/research/:id/cancel` complete the lifecycle. Reading needs `contacts.view`; creating and cancelling need `research.run`. **Cancel contract:** the API sets Redis key `research:cancelled:<job_id>` (24 h) before marking the job cancelled and releasing credits; the planner and executor must check that key before each step and before recording usage, because the envelope may already be queued (ADR-0008).

## Public API v1 (Phase 8)
POST /v1/research · POST /v1/research/parse · GET /v1/research/{id} · GET /v1/research/{id}/results · POST /v1/research/{id}/cancel · GET /v1/leads · GET/PATCH /v1/leads/{id} · GET /v1/companies/{id} · GET /v1/companies/lookup?domain= · GET /v1/companies/{id}/similar · POST /v1/enrich · POST /v1/verify/emails · POST /v1/lists · POST/GET /v1/lists/{id}/items · POST /v1/saved-searches · POST /v1/exports · GET /v1/exports/{id} · GET /v1/jobs/{id} · GET /v1/usage · POST /v1/webhooks

- Rate limit headers `X-RateLimit-Limit/Remaining/Reset`, 429 + `Retry-After`.
- Webhook events: `research.completed, research.failed, lead.new_from_saved_search, export.ready, signal.detected, credits.low`. Signed `X-LF-Signature: t=<ts>,v1=<hmac_sha256>`; retries with backoff for 24 h; delivery log + replay.
- Versioning: `/v1`, additive changes only; `Sunset` header + 12 months notice for removals.

## SSE progress
- Implemented (task 1.10) in `apps/api/src/common/sse/`: one shared Redis subscriber per API process (`ProgressHub`, channels reference-counted; max 5 streams per user, 500 per process, 429 beyond). The stream sends `state`, then buffered and live `progress`, then `done`. Heartbeats re-read the persisted state, so a lost terminal event still ends the stream. Max duration is `PROGRESS_STREAM_MAX_MS` (default 10 min). The browser reaches it through the Next.js server proxy, because EventSource cannot send the bearer header. Dev demo: `POST /app/dev/ping-job` -> `GET /app/dev/ping-job/:id/events` (not available in production).
- `GET /app/research/:id/events` authorizes, subscribes to Redis `progress:{id}`, sends `event: progress` messages, heartbeats every 15 s, ends with `event: done`. On reconnect, first send current persisted state.

## Credits in API
- On job create: estimate -> `reserve` ledger row in same transaction as job row; reject with 402 if insufficient.
- Workers write `usage_events`; the API's credits module (or a DB function) converts usage to `consume` ledger rows idempotently; job finish -> `release` remaining reservation. Details: `11-BILLING-CREDITS.md`.
