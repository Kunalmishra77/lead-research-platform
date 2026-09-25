Written for: whoever is putting this on the VPS — you, today.

# Deploying LeadForge on Coolify

## Before you start, two things worth knowing

**These Docker files have never been built.** The machine they were written on has no Docker
(ADR-0002), so they are written to work on the first try rather than to produce small images —
one build stage per service, no clever pruning. Expect one or two rounds of fixing on the first
build anyway. The "when a build fails" section below lists the places it is most likely to break.

**Four things run, and the product is broken without any one of them.**

| | why it has to run |
| --- | --- |
| `redis` | the job queue. Jobs are Redis streams; lose it and queued work is gone |
| `workers` | the consumer that plans and runs research. Without it a job is created and **nothing ever runs it** — the page shows a job that never finishes and no leads at all |
| `api` | REST, plus the SSE progress stream that stays open for minutes |
| `web` | the Next.js app |

That second row is why this cannot go on Vercel: serverless has no process to sit on a stream.
Postgres is not in the stack — that is Supabase, already hosted.

---

## 1. Supabase, once

You already have the project. Confirm these, because the API will not start without them:

- **Project settings → API**: the project URL and the publishable key.
- **Project settings → Storage → S3 access keys**: create one. The API requires
  `S3_ENDPOINT`, `S3_REGION`, `S3_BUCKET_RAW`, `S3_BUCKET_EXPORTS`, `S3_ACCESS_KEY_ID` and
  `S3_SECRET_ACCESS_KEY` with no defaults.
- **Database → Connection string**: you need three, for three roles with different grants
  (ADR-0003). Use the transaction pooler (port 6543) for `app_api` and `app_worker`; the
  migration URL uses the direct connection (5432).

## 2. Environment in Coolify

Copy `deploy/env.production.template` into Coolify's environment for this resource and fill it
in. Do not put it in a file in the repo — `.env` is git-ignored precisely so a secret does not
become a commit.

Leave **`GOOGLE_PLACES_ENABLED=false`** for the first deploy. Everything comes up and works; a
search simply reports that no source can find businesses. Turn it on once the stack is proven —
that flag is what lets a search spend real money at Google.

## 3. Database, once per deploy

Migrations run as the table owner, which neither app role is, and they must run exactly once —
an API that migrated on boot would race with itself the moment there were two of it.

Run `deploy/migrate.Dockerfile` as a one-off job with `DATABASE_URL_MIGRATIONS` set. It does:

```
pnpm infra:bootstrap    # schema, extensions, roles — idempotent
pnpm db:migrate         # applies what is pending
pnpm db:seed            # sources, plans, credit rates, industries, geography — idempotent
```

The seed is not optional. Without it there are no credit rates (so a lead costs nothing), no
`sources` rows (so no value can be stored at all — `field_values.source_id` is a foreign key),
and no geography (so the planner cannot turn "Delhi" into an area to search).

## 4. Deploy the stack

Point Coolify at this repo with `deploy/docker-compose.yml` as the compose file and the **repo
root** as the build context. Expose only `web` publicly; `api`, `workers` and `redis` talk over
the internal network. The browser never calls the API directly — it goes through the app's own
`/api/app` proxy, which is what keeps the Supabase token server-side (ADR-0002).

Health check for `api`: `GET /health/ready`. It checks the database and Redis, so a container
that is up but cannot reach either reports unhealthy instead of quietly failing every request.

## 5. Prove it works, in this order

Each step only makes sense if the one before it passed.

1. **`api` is healthy.** `/health/ready` returns ok. If not, it is almost always a missing
   required env var — the logs name it.
2. **`workers` are consuming.** The logs say `workers starting` with
   `job_types=['discovery.places_text_search', 'research.plan', 'system.ping']`. If
   `discovery.places_text_search` is missing, the executor did not register and a job will never
   find anything.
3. **Sign up** on the web app and create an org. The free plan grants 50 credits.
4. **Parse works.** `/research/new` → type a request → "Understand this". This calls the model
   but charges no credits. If this fails, `OPENAI_API_KEY` is wrong or the `interactive` worker
   pool is not running.
5. **Turn on `GOOGLE_PLACES_ENABLED=true`** and redeploy the workers.
6. **Run one search.** Watch the counters move on the job page, then read the leads.

---

## The click path, in order

Coolify's exact menu wording changes between versions, so this says what has to happen rather
than pretending to know which button says it. If a label does not match, look for the thing that
does the same job.

### Step 0 — before Coolify, gather the values

Almost all of them are already in your local `.env` and already work. Copy them across rather
than making new ones:

`SUPABASE_URL`, `SUPABASE_JWKS_URL`, `SUPABASE_PUBLISHABLE_KEY`, `DATABASE_URL`,
`DATABASE_URL_WORKERS`, `DATABASE_URL_MIGRATIONS`, all six `S3_*`, `OPENAI_API_KEY`,
`GOOGLE_PLACES_API_KEY`, `SERPER_API_KEY`.

Two you must **not** copy, because they decide what the public site thinks it is:

- `APP_URL` — the public https URL, exactly as the browser will show it, no trailing slash.
- `API_URL` — the same public URL.

You also need a domain (or subdomain) with an A record pointing at the VPS. Coolify issues the
certificate once DNS resolves.

### Step 1 — point Coolify at the repo

New project → new resource → the **Docker Compose from a Git repository** option.

- Repository: your GitHub repo, branch `main`
- Compose file: `deploy/docker-compose.yml`
- Build context / base directory: the **repository root**, not `deploy/`. Compose resolves a
  relative build context against the project directory, which Coolify sets to the repo root, so
  every `context:` in the compose file is `.`. The Dockerfiles copy
  workspace packages from outside `apps/`, so a context of `deploy/` cannot see them.

Coolify will read the compose file and show four services: `redis`, `api`, `workers`, `web`.

### Step 2 — environment

Paste `deploy/env.production.template`, filled in, into the environment for the **resource**, so
every service gets it. Compose passes each service only the keys it needs.

Keep `GOOGLE_PLACES_ENABLED=false` for the first deploy.

### Step 3 — the domain goes on `web` only

Give the domain to the `web` service. Leave `api`, `workers` and `redis` without one: the browser
never calls the API directly, it goes through the app's own `/api/app` proxy (ADR-0002).

**`APP_URL` must equal that domain exactly, including `https://`.** This is the single most
likely thing to break the first deploy, and the symptom is confusing: pages load, then every
sign-in or form submit returns 403. The app compares the browser's `Origin` header against
`APP_URL` as a CSRF check, and `https://app.example.com` does not equal `https://app.example.com/`
or the `http://` version. The cookie's `secure` flag is decided by the same value, so a mismatch
also breaks staying signed in.

### Step 4 — the database, once

If your Supabase project is the same one you have been developing against, **this is already
done** — you have run `pnpm db:migrate` and `pnpm db:seed` against it. Skip to step 5.

If it is a fresh project, run `deploy/migrate.Dockerfile` as a one-off job with
`DATABASE_URL_MIGRATIONS` set, before the first deploy of the stack.

A note worth making deliberately rather than by accident: using your dev Supabase project as
production means demo data, test orgs and real data live in one database, and a mistake made
while developing lands on the live site. For a demo today that is a reasonable trade. It is not
one to leave in place.

### Step 5 — deploy, then check in this order

Each check only means something if the one before it passed.

1. `api` healthy — `/health/ready`. A failure here is almost always a missing required variable,
   and the log says which.
2. `workers` log shows `workers starting` with
   `job_types=['discovery.places_text_search', 'research.plan', 'system.ping']`.
   If `discovery.places_text_search` is absent, a job will never find anything.
3. Open the domain, sign up, create an org. The free plan grants 50 credits.
4. `/research/new` → type a request → **Understand this**. No credits are charged. A failure here
   means `OPENAI_API_KEY` or the `interactive` worker pool.
5. Only now set `GOOGLE_PLACES_ENABLED=true` and redeploy the workers.
6. Run one search at **quick** depth, which is 1 credit a lead rather than 3.

## When a build fails

The likely places, in order:

- **[WARN] Unsupported engine: wanted: {"node":">=24"} (current: {"node":"v22.18.0","pnpm":"11.11.0"})
[WARN] Unsupported engine: wanted: {"node":">=24"} (current: {"node":"v22.18.0","pnpm":"11.11.0"})
undefined
[ERR_PNPM_RECURSIVE_EXEC_FIRST_FAIL] Command "build:packages" not found

Did you mean "pnpm build:packages"? exits 1 with almost no output.** turbo publishes glibc binaries only
  (,  — no musl build), and these images are Alpine. Each one
  installs  for that reason; without it the turbo binary cannot start and the failure
  looks like a compile error with no compiler output.

- **`lstat /artifacts/deploy: no such file or directory`**, with "Dockerfile not found for service
  api at ../deploy/Dockerfile.api" just above it. A build context is relative to the project
  directory, not to the compose file, and Coolify sets the project directory to the repo root. Every
  `context:` must therefore be `.`, never `..`.

- **The install succeeds and the next step cannot find `turbo`, `tsc`, `nest` or `next`.**
  Something put `NODE_ENV=production` into the build environment, so pnpm installed a
  production-only tree. On Coolify this is not hypothetical: it injects an ARG declaration for every
  environment variable before building, and an ARG is visible to RUN as an environment variable —
  its own build log warns about this. Every install here passes `--prod=false` for that reason;
  do not remove it.

- **`pnpm install --frozen-lockfile` fails.** The lockfile and the manifests disagree — usually
  because a `package.json` was edited without running `pnpm install`. Run it locally, commit the
  lockfile, redeploy.
- **A workspace package is missing at build.** The Dockerfiles copy each `package.json`
  explicitly. If a new package is added to the workspace, add its `COPY` line too.
- **`uv sync --frozen` fails in the workers image.** The `uv` version in the Dockerfile is pinned
  and must be at least as new as the one that wrote `services/workers/uv.lock`.
- **The web build cannot find `@leadforge/contracts`.** `pnpm build:packages` must run before the
  app build; it is in the same `RUN` line for that reason.
- **The API starts and immediately exits.** Read the first lines of the log: the env schema
  refuses to start with a missing key and says which one.

## What this deploys, and what it does not

What runs: a research job goes from a sentence to real businesses in the database, each value
carrying where it came from, with credits reserved before the run and charged per lead delivered.

What is not built yet, and will be asked about:

- **Export.** No CSV, no Sheets, no API to pull leads out. That is Phase 7.
- **Emails.** Discovery finds what a maps listing carries — name, address, phone, website,
  rating. Emails come from crawling the website, which is Phase 3.
- **Searching your own leads.** The results grid, filters, lists and tags are Phase 4.
- **Scoring.** Phase 6.

Phases 3–7 are the MVP and are roughly 56 person-weeks on the roadmap. This deploy is Phase 2.
