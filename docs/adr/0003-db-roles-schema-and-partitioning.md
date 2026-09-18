# ADR-0003: Database roles, tenant bootstrap and partitioning

- Status: Accepted
- Date: 2026-09-18

## Context
docs/04 left gaps that Phase 1 hits immediately:
- Postgres requires every unique constraint/PK on a partitioned table to include the partition key, so `usage_events unique(research_job_id, meter, unit_key)` and the plain `id` PKs of `audit_logs`/`usage_events` cannot exist as written on monthly partitions.
- Role names differ between `.env.example` (`leadforge`, `leadforge_worker`) and phase-01 (`app_api`, `app_worker`); no migration owner is defined.
- Signup must create an organization before any tenant context exists, and the admin shell must read across orgs, but no privileged path is defined.
- `credit_ledger.reason` values differ between docs/04 and docs/11.
- `organizations` has no `org_id` column, and auth tables are global.

## Options considered
1. Partitioning: (a) no partitions until Phase 10: simple now, painful conversion of large tables later; (b) partition `audit_logs` and `usage_events` from day one, with idempotency moved to a small non-partitioned key table.
2. Privileged operations: (a) a BYPASSRLS role used by the API: broad, easy to misuse; (b) narrow `SECURITY DEFINER` functions, each of which writes an audit log row.

## Decision
- **Roles:** Supabase `postgres` owns the tables and runs migrations only. `app_api` (API) and `app_worker` (workers) are login roles without BYPASSRLS. Every tenant table uses `ENABLE` + `FORCE ROW LEVEL SECURITY`. The `.env.example` names follow these roles.
- **Schema:** everything lives in schema `app` (see ADR-0002).
- **Tenant context:** `withTenant(orgId, tx => ...)` runs `SET LOCAL app.org_id` (and `app.user_id`) at the start of the transaction. Workers set it from the envelope for tenant writes.
- **`organizations` RLS:** the policy is on `id = current_setting('app.org_id')::uuid`. `user_profiles` is readable only by the user themselves or through admin functions.
- **Privileged paths:** `SECURITY DEFINER` functions owned by `postgres` with a fixed `search_path`:
  - `app.bootstrap_org(user_id, name)` creates org + default workspace + owner membership + audit row.
  - `app.admin_*` read functions check `user_profiles.is_platform_staff` and write an audit row.
  - Only `app_api` gets EXECUTE on them.
- **Partitioning:** `audit_logs` and `usage_events` are partitioned monthly by `created_at` from day one, with PK `(id, created_at)`. Usage idempotency uses a non-partitioned `usage_unit_keys(research_job_id, meter, unit_key)` primary key, inserted in the same transaction as the usage event. The migration pre-creates 12 monthly partitions plus a default partition. Future partitions are kept 3 months ahead by `pg_partman` + `pg_cron` (both available on the Supabase project); a worker job is the fallback if either can't be enabled. The other partitioned tables (`field_values`, `raw_documents`) are decided when they are created (Phase 3), under the same rule.
- **Ledger reasons:** use the docs/11 superset: grant, purchase, subscription_renewal, reserve, consume, release, refund, expire, adjustment.

## Consequences
- docs/04 must be updated: the `usage_events` constraint, `usage_unit_keys`, PK notes, ledger reasons and the `organizations` policy.
- The RLS isolation test (task 1.8) must also cover partition children and prove that `app_api` cannot call admin functions without the staff flag.
- Whether Supavisor (the pooler) accepts custom login roles is verified in task 1.6. If it doesn't, the fallback is a `SET ROLE` right after connecting, documented in an ADR amendment.
