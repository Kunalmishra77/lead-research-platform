# 03 — Folder Structure

Create folders when a phase needs them; do not scaffold empty modules early.

```text
lead-research-platform/
├── CLAUDE.md  README.md  PROGRESS.md  .env.example  .gitignore
├── package.json  pnpm-workspace.yaml  turbo.json  tsconfig.base.json
├── .claude/ (settings.json, commands/, agents/)
├── .github/workflows/ci.yml
├── apps/
│   ├── web/
│   │   └── src/
│   │       ├── app/
│   │       │   ├── (auth)/login  signup  forgot-password
│   │       │   ├── (app)/layout.tsx
│   │       │   ├── (app)/dashboard
│   │       │   ├── (app)/research/new  research/[id]  research/history
│   │       │   ├── (app)/search            # search stored leads
│   │       │   ├── (app)/companies/[id]  people/[id]
│   │       │   ├── (app)/lists  lists/[id]
│   │       │   ├── (app)/saved-searches
│   │       │   ├── (app)/exports  integrations  api-keys  billing
│   │       │   └── (app)/settings/(profile|workspace|team|privacy)
│   │       ├── features/                   # feature folders
│   │       │   ├── research/ (components, hooks, api.ts, types.ts)
│   │       │   ├── results-grid/
│   │       │   ├── spec-builder/
│   │       │   ├── provenance/
│   │       │   ├── lists/  exports/  billing/  auth/
│   │       ├── components/ui/              # shadcn
│   │       ├── lib/ (api-client, sse, format, auth-client)
│   │       └── styles/
│   └── api/
│       └── src/
│           ├── main.ts  app.module.ts
│           ├── modules/
│           │   ├── auth/  orgs/  workspaces/  members/
│           │   ├── research/        # parse, estimate, create job, SSE progress
│           │   ├── leads/  companies/  people/
│           │   ├── lists/  tags/  notes/
│           │   ├── scoring/  saved-searches/  signals/
│           │   ├── exports/  integrations/  crm/
│           │   ├── api-keys/  webhooks/  public-api/
│           │   ├── billing/  credits/  usage/
│           │   ├── audit/  privacy/  admin/
│           ├── common/ (guards, decorators, interceptors, filters, pagination, errors)
│           ├── infra/
│           │   ├── db/ (drizzle client, tenant-context.ts, schema/ -> re-exports db/schema)
│           │   ├── redis/  streams/ (publisher)  storage/  crypto/ (envelope encryption)
│           └── config/
├── services/
│   └── workers/
│       ├── pyproject.toml
│       └── app/
│           ├── main.py                  # starts pools by WORKER_POOLS env
│           ├── config.py  logging.py  telemetry.py
│           ├── jobs/ (consumer.py, publisher.py, retry.py, envelope.py, registry.py)
│           ├── db/ (engine.py, tables.py reflect-or-core, tenant.py, repos/)
│           ├── storage/  cache/  ratelimit/  metering/
│           ├── orchestrator/ (spec_parser.py, planner.py, executor.py, critic.py, budgets.py, templates/)
│           ├── connectors/
│           │   ├── base.py  registry.py  http_client.py (metered + rate-limited)
│           │   ├── google_places/  serp/  website/  companies_house/  opencorporates/
│           │   ├── edgar/  ats_boards/
│           ├── crawler/ (frontier.py, robots.py, fetch_http.py, fetch_browser.py, restrictions.py, raw_store.py)
│           ├── extract/ (jsonld.py, contacts.py, socials.py, tech_detect/, llm_extract.py, page_classifier.py)
│           ├── normalize/ (phone.py, email.py, url.py, address.py, name.py, industry.py)
│           ├── resolve/ (blocking.py, features.py, matcher.py, adjudicator.py, cluster.py, merge.py)
│           ├── verify/ (email.py, phone.py, website.py, company.py, social.py, confidence.py)
│           ├── enrich/ (base.py, waterfall.py, modules/)
│           ├── score/ (engine.py, website_quality.py)
│           ├── signals/  export/ (csv.py, xlsx.py, sheets.py, json.py)  crm/
│           ├── ai/ (gateway.py, router.py, cache.py, prompts/, schemas/, evals/)
│           └── tests/ (unit/, integration/, fixtures/)
├── packages/
│   ├── contracts/   # JSON Schemas: research-spec, job-envelope, field-catalogue, progress-event, scoring-model
│   │   ├── schemas/  generated/ts/  generated/python/  scripts/gen.ts
│   ├── ui/
│   └── config/ (eslint, tsconfig, tailwind preset)
├── db/
│   ├── schema/      # drizzle schema files (source of truth)
│   ├── migrations/
│   ├── rls/         # SQL policies, applied as migrations
│   └── seeds/       # sources, industries, technologies, demo org
├── infra/
│   └── setup/ (SETUP.md, bootstrap SQL for schema/roles, bucket + connectivity check scripts)
└── docs/ (numbered specs, adr/)  phases/
```

## Naming
- TS files kebab-case; React components PascalCase; Python snake_case.
- DB tables snake_case plural; columns snake_case; FKs `<entity>_id`.
- Streams `jobs:<pool>`; job types `<domain>.<action>` (e.g. `crawl.fetch`, `enrich.website_profile`).
