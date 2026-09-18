# Dev environment setup (no Docker)

Decisions: `docs/adr/0002-hosted-dev-services-and-supabase-auth.md`, `docs/adr/0003-db-roles-schema-and-partitioning.md`.

| Service | Dev | How |
| --- | --- | --- |
| Postgres 17 + extensions | Supabase project (Mumbai) | hosted; `pnpm infra:bootstrap` prepares it |
| Auth | Supabase Auth | hosted; JWKS verified by the API |
| Object storage | Supabase Storage (S3 API) | hosted; `pnpm infra:buckets` |
| Redis 8 | portable Redis for Windows | `pnpm redis:start` (downloads once to `%LOCALAPPDATA%\leadforge\redis`) |

## One-time setup

1. Install Node 24 LTS, pnpm 11, Python 3.12 (`uv python install 3.12`), uv.
2. Copy `infra/setup/env.template` to `.env` in the repo root and fill it in:
   - Supabase dashboard -> **Connect**: session pooler string (`DATABASE_URL_MIGRATIONS`).
   - Generate two random passwords for `APP_API_DB_PASSWORD` / `APP_WORKER_DB_PASSWORD`
     (`node -e "console.log(crypto.randomBytes(24).toString('base64url'))"`).
   - **Project Settings -> API Keys**: publishable key.
   - **Storage -> S3 Connection**: enable, create an access key.
   - **Project Settings -> JWT Keys**: signing key must be asymmetric (ES256). New projects already are.
   - **Authentication -> URL Configuration**: Site URL `http://localhost:3000`, redirect `http://localhost:3000/**`.
   - **Authentication -> Emails -> Templates**: point the links at our own domain, because some ISPs block `supabase.co` in the browser (ADR-0002). In **Confirm signup**, replace the link with
     `<a href="{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=email">Confirm your email</a>`.
     In **Reset password**, use the same link with `type=recovery`. (Without this change the default link still works where `supabase.co` is reachable, via the `?code=` fallback.)
3. `pnpm install`
4. In a separate terminal: `pnpm redis:start` (leave it running).
5. `pnpm infra:bootstrap` (schema `app`, extensions, roles `app_api` / `app_worker`; idempotent).
6. `pnpm infra:buckets` (private buckets `lf-raw`, `lf-exports`).
7. `pnpm infra:check` -> every line must say PASS.

## Platform staff (admin shell)

Until an admin UI exists, grant the flag by hand in the Supabase SQL editor (after task 1.6 creates the table):

```sql
update app.user_profiles set is_platform_staff = true
where user_id = (select id from auth.users where email = 'you@example.com');
```

## Sentry (optional error reporting)
Sentry is off until a DSN is set. Only errors are sent. Credentials, cookies, request bodies, query strings and user fields other than the id are stripped first. Worker exception text goes through the same redaction as the logs.

1. Sign up at https://sentry.io (the free Developer plan is enough) and create an organization.
2. Create three projects, one per service, so errors stay separate:
   - platform **Next.js**, named `leadforge-web`
   - platform **NestJS**, named `leadforge-api`
   - platform **Python**, named `leadforge-workers`

   You can skip the in-app setup wizard; the SDKs are already installed.
3. Copy each project's DSN (Project Settings -> Client Keys (DSN)) into `.env` as `SENTRY_DSN_WEB`, `SENTRY_DSN_API` and `SENTRY_DSN_WORKERS`.
4. Optional: set `SENTRY_ENVIRONMENT` (default `development`). Leave `SENTRY_TRACES_SAMPLE_RATE=0` unless you want performance traces.
5. Restart the services. For the browser, rebuild the web app too: the web DSN is inlined at build time.
6. What gets reported: unexpected API errors (5xx), failed page renders, route handlers and server actions in the web app, and jobs that fail for good in the workers. Expected outcomes such as validation errors, 4xx responses, compliance stops (`access_restricted`) and budget pauses are not reported. Reported errors show up under each project's **Issues**.

Source maps are not uploaded (no `SENTRY_AUTH_TOKEN`), so browser stack traces stay minified. We will add that with the deploy pipeline.

## Troubleshooting

- **`SSL/TLS connection failed` or `UNEXPECTED_EOF` for `*.supabase.co`**: your ISP sinkholes the domain.
  Keep `DEV_DNS_OVER_HTTPS=true` (Node and Python resolve those hosts via DoH), and for your browser
  enable Secure DNS (Chrome: Settings -> Privacy -> Use secure DNS -> Cloudflare). The DB pooler
  host (`*.pooler.supabase.com`) is not affected.
- **Free Supabase project paused** after inactivity: restore it from the dashboard, then `pnpm infra:check`.
- **`permission denied` as app_api**: privileges on tables come from migrations (default privileges
  set by bootstrap plus per-table revokes/grants). Check the table's grants in `db/migrations`; re-running
  bootstrap never re-grants existing tables.
- **Every web page returns 404 in `pnpm dev`** after the dev server was killed hard: the Turbopack
  dev cache is stale. Stop the server, delete `apps/web/.next`, start again.
- **`Invalid environment configuration` from the API**: the API reads the repo-root `.env`; check the
  file exists there. Variables exported in the shell also work (`turbo.json` passes them through).
