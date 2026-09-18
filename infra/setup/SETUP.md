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

## Troubleshooting

- **`SSL/TLS connection failed` or `UNEXPECTED_EOF` for `*.supabase.co`**: your ISP sinkholes the domain.
  Keep `DEV_DNS_OVER_HTTPS=true` (Node and Python resolve those hosts via DoH), and for your browser
  enable Secure DNS (Chrome: Settings -> Privacy -> Use secure DNS -> Cloudflare). The DB pooler
  host (`*.pooler.supabase.com`) is not affected.
- **Free Supabase project paused** after inactivity: restore it from the dashboard, then `pnpm infra:check`.
- **`permission denied` as app_api**: re-run `pnpm infra:bootstrap` (it re-applies grants).
