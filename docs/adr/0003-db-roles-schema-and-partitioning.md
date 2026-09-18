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
- Amendment 2026-09-18 (task 1.6, implemented in `db/migrations/0001_*`, `0002_security.sql`):
  - Partitions come from our own `app.ensure_monthly_partitions(regclass, months_ahead)` plus a nightly pg_cron job, not pg_partman (no extra schema to secure). App roles get no privileges on partition children, because parent RLS does not apply when a child is queried directly.
  - All `app` tables have RLS enabled and forced, global ones included. `sources` has permissive policies: read for both roles, write for `app_worker`.
  - Pre-org access: users can read their own memberships (`memberships_self_read`), plus the orgs and workspaces they belong to.
  - `user_profiles`: `app_api` may insert `(user_id, full_name)` and update `full_name` only, never `is_platform_staff`.
  - `bootstrap_org` requires `p_user_id = app.current_user_id()` and a confirmed email in `auth.users`, and caps a user at 10 owned orgs.
- Hardening after the security review (migrations `0003_tenant_integrity`, `0004_security_hardening`):
  - Composite `(id, org_id)` foreign keys on memberships, research_jobs, usage_unit_keys and usage_events, so no tenant row can reference another org's rows.
  - Partition children have RLS forced with no policies (deny-all).
  - The bootstrap script no longer issues blanket grants on existing tables.
  - Only `organizations.name`/`slug` are updatable by `app_api`, and `app_api` cannot insert ledger rows.
  - Audit inserts cannot claim another actor.
  - `bootstrap_org` rejects banned/deleted users and takes a per-user advisory lock.
  - `withTenant` re-checks membership in the database when a user is set.
- Trust boundary (task 1.8): RLS protects against missing or wrong tenant context in application code, not against a compromised app role. `app_api` can call `set_config('app.org_id', ...)` itself, so the API remains trusted to set the context from the verified token and membership. `withTenant` re-checks membership whenever a user is given.
- A user who belongs to several orgs can see the names of those orgs and workspaces, and their own memberships, even inside another org's context (needed for the switcher). Tenant data (jobs, ledger, usage, audit) stays strictly scoped to the active org.
- docs/04 must be updated: the `usage_events` constraint, `usage_unit_keys`, PK notes, ledger reasons and the `organizations` policy.
- The RLS isolation test (task 1.8) must also cover partition children and prove that `app_api` cannot call admin functions without the staff flag.
- Whether Supavisor (the pooler) accepts custom login roles is verified in task 1.6. If it doesn't, the fallback is a `SET ROLE` right after connecting, documented in an ADR amendment.
