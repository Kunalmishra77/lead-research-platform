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

## When a build fails

The likely places, in order:

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
