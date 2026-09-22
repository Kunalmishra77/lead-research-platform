# ADR-0007: Company graph and reference data tables (Phase 2)

- Status: Accepted
- Date: 2026-09-22

## Context
Phase 2 stores the first discovered businesses. docs/04 describes the global company graph, but a few points were open or had to change for our setup:
- ADR-0003 requires RLS enabled and forced on every table in schema `app`, including tables that are shared across tenants.
- Search tiling needs city and state bounding boxes; docs/04 had no table for geography.
- docs/04 lists `citext`, PostGIS `geography` and `vector(1024)` columns on the graph tables. Phase 2 does not query by distance or embeddings yet.

## Options considered
1. Build every docs/04 column now (citext, geography, embeddings, full-text search): complete, but adds extension-typed columns nothing reads until Phases 4-6, and ties migrations to the `extensions` schema earlier than needed.
2. Build only what Phase 2 reads and writes, keep types plain, and add the rest with the phase that uses it.

## Decision
Option 2.
- **Graph tables** (`companies`, `company_domains`, `company_locations`, `field_values`) are global: RLS forced with `USING (true)` read policies for `app_api` and `app_worker`, and write policies for `app_worker` only. The API never writes the graph. Workers fill it only from public sources and official APIs, so tenant-private data cannot enter it (CLAUDE.md tenant rule).
- **Graph write policy:** workers write the graph only from connector output (public pages and official APIs), never from `searches.spec`, user input or tenant imports. User corrections stay in the tenant layer (`leads.overrides`) until reviewed (docs/04). Licensed `provider` data whose licence is per-tenant must stay tenant-side. The 2.11 upsert code enforces this and is tested.
- **Database guards on `field_values`:** `method = 'ai'` requires `model` and `prompt_version`, and may never be an email, phone or WhatsApp value; `derivation` is one of `found`, `derived_pattern`, `provider`, `user`; `source_url` must be http(s); `observed_at` has no default (observation time, not ingestion time).
- **Reference tables** (`industries`, `geo_areas`) are seeded by the owner role and read-only for both app roles.
- **`geo_areas`** (new): country, state, district, city and locality rows with a bounding box (`min/max lat/lng`) and aliases. The planner tiles these boxes for Places searches.
- **Domains** are plain `text` stored lower-case and enforced by a check constraint, instead of `citext`.
- **Locations** store `lat`/`lng` as double precision. A PostGIS `geom` column and GiST index arrive with map/radius search (Phase 4).
- **Embeddings and `search_tsv`** arrive with the phases that use them (4 and 6).
- **`field_values`** is partitioned monthly on `observed_at` (ADR-0003 pattern). Provenance columns are NOT NULL (`source_id`, `source_url`, `method`, `confidence`, `observed_at`). A check requires `model` and `prompt_version` when `method = 'ai'`. Rows are append-only: workers may only update `is_current`.
- **Tenant tables** `searches` and `research_tasks` follow the composite `(id, org_id)` foreign-key rule. A task's parent must be in the same job (`(parent_task_id, research_job_id)` FK), so tasks form a tree per job; fan-in would need an edge table later.
- **Upsert keys** `companies.primary_domain` and `company_locations.google_place_id` are plain unique constraints (NULLs never conflict), so `ON CONFLICT (column)` works without a partial-index predicate. Domains are bare lower-case hosts without `www.` (check constraint), so `www.x.com` and `x.com` cannot become two companies.

## Consequences
- Adding PostGIS, citext or vector columns later is an additive migration.
- A misbehaving worker could write wrong public data into the shared graph, but never another tenant's data. Provenance on every value keeps bad writes traceable and reversible.
- The RLS test suite now also asserts graph readability from every org, API write denial and reference-data immutability.
