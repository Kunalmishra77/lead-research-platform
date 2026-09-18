# 04 — Database

PostgreSQL 16 with extensions: `pgvector`, `pg_trgm`, `postgis`, `citext`, `pgcrypto`. Drizzle owns schema and migrations (`db/schema`, `db/migrations`). Python reads/writes with SQLAlchemy Core using the same table names.

## Conventions
- PK `id uuid` (UUID v7 generated in app). `created_at`, `updated_at timestamptz default now()`.
- Tenant tables: `org_id uuid not null`, `workspace_id uuid` where relevant, RLS enabled + forced.
- Soft delete only where users can restore (`deleted_at`); hard-delete jobs handle privacy erasure.
- jsonb for sparse/variable data; never for fields that are filtered often (promote to columns).
- Big append-only tables partitioned monthly: `field_values`, `raw_documents`, `usage_events`, `audit_logs`.

## Global company graph (no RLS)
| Table | Key columns | Constraints / indexes |
| --- | --- | --- |
| sources | id, key, name, type (api/crawl/registry/provider/user), tos_class (green/amber/red), legal_approved bool, reliability jsonb, cost_per_call_micros, default_ttl_days, enabled | unique(key) |
| industries | id, parent_id, name, slug, naics_code, synonyms text[] | unique(slug) |
| technologies | id, name, category, fingerprint jsonb | unique(name) |
| companies | id, canonical_name, normalized_name, primary_domain citext, country, state, city, industry_id, employee_band, company_status, best jsonb (computed best values), confidence real, verification_status, last_verified_at, embedding vector(1024), search_tsv tsvector, merged_into_id | partial unique(primary_domain) where primary_domain is not null; gin(search_tsv); gin(normalized_name gin_trgm_ops); hnsw(embedding); btree(country, city, industry_id) |
| company_domains | company_id, domain citext, is_primary, is_platform bool | unique(domain) where not is_platform |
| company_locations | id, company_id, address, city, state, postal_code, country, geom geography(Point,4326), google_place_id, phone_e164 | unique(google_place_id); gist(geom); btree(company_id) |
| people | id, company_id, full_name, normalized_name, title, seniority, department, best jsonb, confidence | btree(company_id, seniority); gin trgm(normalized_name) |
| contacts | id, company_id, person_id, kind (email/phone/whatsapp/contact_form), value_normalized, derivation (found/derived_pattern/provider/user), verification_status, verified_at, is_role_account, suppressed bool | unique(kind, value_normalized, coalesce(person_id, company_id)); btree(value_normalized) |
| social_profiles | id, company_id, person_id, platform, handle, url, followers, last_activity_at, match_status (verified_link/probable/unverified) | unique(platform, lower(handle)) |
| websites | id, company_id, url, status, http_status, ssl_valid, is_parked, technologies jsonb, content_hash, quality_score, last_crawled_at | unique(url) |
| raw_documents | id, url, url_hash bytea, source_id, fetched_at, http_status, content_type, content_hash bytea, storage_key, bytes, access_status (ok/restricted/error) | unique(url_hash, content_hash); btree(url_hash, fetched_at desc); partition by fetched_at |
| field_values | id, entity_type (company/person/location), entity_id, field, value jsonb, source_id, source_url, raw_document_id, method (api/crawl/ai/user/provider), derivation, confidence real, observed_at, is_current bool, model, prompt_version | btree(entity_type, entity_id, field) where is_current; partition by observed_at |
| entity_merges | id, entity_type, winner_id, loser_id, score, method (rule/ai/human), actor_user_id, created_at, undone_at | btree(loser_id) |
| match_candidates | id, entity_type, a_id, b_id, score, features jsonb, status (pending/merged/rejected/unsure) | unique(entity_type, least(a_id,b_id), greatest(a_id,b_id)) |
| signals | id, company_id, type, detected_at, evidence_url, payload jsonb, confidence | btree(company_id, detected_at desc); btree(type, detected_at desc) |
| suppression_list | id, value_hash bytea, kind, scope (global/org), org_id nullable, reason, created_at | unique(value_hash, scope, org_id) |

## Tenant layer (RLS)
| Table | Key columns | Constraints / indexes |
| --- | --- | --- |
| organizations | id, name, slug, plan, region, credits_balance bigint (cache of ledger) | unique(slug) |
| workspaces | id, org_id, name, settings jsonb | btree(org_id) |
| users / sessions / accounts | managed by Better Auth tables | unique(email) |
| memberships | id, org_id, workspace_id, user_id, role (owner/admin/manager/member/viewer) | unique(workspace_id, user_id) |
| searches | id, org_id, workspace_id, user_id, raw_query, spec jsonb, spec_version | btree(workspace_id, created_at desc) |
| research_jobs | id, org_id, workspace_id, search_id, status (queued/planning/running/paused/completed/failed/cancelled), depth, credit_budget, credits_reserved, credits_used, cost_micros, progress jsonb, error_class, started_at, finished_at | btree(org_id, status) |
| research_tasks | id, org_id, research_job_id, parent_task_id, type, input jsonb, status, attempts, cost_micros, output jsonb, error_class | btree(research_job_id, status) |
| leads | id, org_id, workspace_id, company_id, person_id, research_job_id, score smallint, score_breakdown jsonb, status, assignee_user_id, overrides jsonb, custom_fields jsonb, contacted_at | unique(workspace_id, company_id, coalesce(person_id, '00000000-0000-0000-0000-000000000000')); btree(workspace_id, score desc); btree(workspace_id, status) |
| lead_events | id, org_id, lead_id, type, actor_user_id, payload jsonb, created_at | btree(lead_id, created_at desc) |
| lists / list_items | id, org_id, workspace_id, name, owner_user_id, visibility / list_id, lead_id, added_by | unique(list_id, lead_id) |
| tags / lead_tags | id, org_id, workspace_id, name, color / lead_id, tag_id | unique(workspace_id, lower(name)) / pk(lead_id, tag_id) |
| notes | id, org_id, lead_id, author_user_id, body | btree(lead_id) |
| custom_columns | id, org_id, workspace_id, name, prompt, output_type | AI research columns (Phase 6) |
| scoring_models | id, org_id, workspace_id, name, version, definition jsonb, is_default | unique(workspace_id, name, version) |
| saved_searches / saved_search_seen | id, org_id, workspace_id, search_id, cron, timezone, mode, actions jsonb, next_run_at, paused / saved_search_id, company_id, first_seen_at | btree(next_run_at) where not paused / pk(saved_search_id, company_id) |
| exports | id, org_id, workspace_id, user_id, format, destination, selection jsonb, columns jsonb, row_count, status, cursor jsonb, storage_key, expires_at | btree(org_id, created_at desc) |
| integrations / integration_credentials | id, org_id, provider, account_label, status / integration_id, ciphertext bytea, wrapped_dek bytea, key_version | unique(org_id, provider, account_label) |
| crm_sync_records | id, org_id, integration_id, lead_id, remote_object, remote_id, status, error, synced_at | unique(integration_id, lead_id, remote_object) |
| api_keys | id, org_id, workspace_id, name, prefix, key_hash bytea, scopes text[], last_used_at, expires_at, revoked_at | unique(prefix) |
| webhooks / webhook_deliveries | id, org_id, url, secret_ciphertext, events text[] / id, webhook_id, event, status, attempts, response_code | btree(webhook_id, created_at desc) |
| usage_events | id, org_id, user_id, research_job_id, meter, units, credits, cost_micros, unit_key, created_at | unique(research_job_id, meter, unit_key); partition monthly |
| credit_ledger | id, org_id, delta bigint, balance_after bigint, reason (grant/purchase/reserve/consume/release/refund/expire), ref_type, ref_id, created_at | append-only; btree(org_id, created_at desc) |
| subscriptions / invoices | provider, provider_ids, status, period_start/end, amounts | unique(provider, provider_subscription_id) |
| audit_logs | id, org_id, actor_user_id, actor_api_key_id, action, target_type, target_id, ip, user_agent, meta jsonb, created_at | append-only; partition monthly |
| privacy_requests | id, requester_email, kind (access/erasure/objection), status, verified_at, completed_at | global table, admin only |

## RLS pattern
```sql
alter table leads enable row level security;
alter table leads force row level security;
create policy tenant_isolation on leads
  using (org_id = current_setting('app.org_id', true)::uuid)
  with check (org_id = current_setting('app.org_id', true)::uuid);
```
- App role `app_api` has no BYPASSRLS. Admin panel uses a separate audited path.
- CI test: create two orgs, attempt cross-org read/write on every tenant table, expect zero rows / error.

## Best-value computation
`companies.best` = per field, the current `field_values` row with the highest confidence (ties -> newest). Recomputed by the pipeline after inserts for that entity; never edited by hand. User overrides live in `leads.overrides` (tenant) and, when marked "correct for everyone", create a `method=user` field value after review.

## Retention jobs
- raw_documents objects: 90 days (keep row metadata 12 months)
- field_values non-current history: 24 months
- unverified person contacts not re-verified: purge after 18 months
- usage_events: 24 months; audit_logs: 36 months
