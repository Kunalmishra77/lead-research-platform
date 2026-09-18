/**
 * Structural security invariants of schema `app` (ADR-0003). Read-only; runs when
 * DATABASE_URL_MIGRATIONS is set (dev/test project locally, Supabase CLI stack in CI).
 */
import postgres from 'postgres';
import { afterAll, describe, expect, it } from 'vitest';

const ownerUrl = process.env.DATABASE_URL_MIGRATIONS;

describe.skipIf(!ownerUrl)('schema app security invariants', () => {
  const sql = postgres(ownerUrl ?? '', { max: 1, prepare: false });

  afterAll(async () => {
    await sql.end();
  });

  it('every app table (not partition children) has RLS enabled and forced', async () => {
    const rows = await sql<{ relname: string; rls: boolean; force: boolean }[]>`
      select c.relname, c.relrowsecurity as rls, c.relforcerowsecurity as force
      from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'app' and c.relkind in ('r', 'p') and not c.relispartition`;
    expect(rows.length).toBeGreaterThanOrEqual(10);
    expect(rows.filter((r) => !r.rls || !r.force).map((r) => r.relname)).toEqual([]);
  });

  it('app roles cannot touch partition children directly (parent RLS does not apply there)', async () => {
    const rows = await sql<{ relname: string }[]>`
      select c.relname
      from pg_class c join pg_namespace n on n.oid = c.relnamespace
      cross join (values ('app_api'), ('app_worker')) as r(role)
      where n.nspname = 'app' and c.relkind = 'r' and c.relispartition
        and (has_table_privilege(r.role, c.oid, 'select') or has_table_privilege(r.role, c.oid, 'insert')
             or has_table_privilege(r.role, c.oid, 'update') or has_table_privilege(r.role, c.oid, 'delete'))`;
    expect(rows.map((r) => r.relname)).toEqual([]);
  });

  it('partition children also have RLS forced (deny-all even if grants reappear)', async () => {
    const rows = await sql<{ relname: string }[]>`
      select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'app' and c.relkind = 'r' and c.relispartition
        and not (c.relrowsecurity and c.relforcerowsecurity)`;
    expect(rows.map((r) => r.relname)).toEqual([]);
  });

  it('monthly partitions exist at least 2 months ahead', async () => {
    const rows = await sql<{ parent: string; ahead: number }[]>`
      select p.relname as parent,
             count(*) filter (where c.relname ~ '_p[0-9]{6}$'
               and to_date(right(c.relname, 6), 'YYYYMM') >= date_trunc('month', now())::date)::int as ahead
      from pg_inherits i
      join pg_class c on c.oid = i.inhrelid
      join pg_class p on p.oid = i.inhparent
      join pg_namespace n on n.oid = p.relnamespace
      where n.nspname = 'app' and p.relname in ('audit_logs', 'usage_events')
      group by p.relname`;
    expect(rows).toHaveLength(2);
    for (const r of rows) expect(r.ahead, r.parent).toBeGreaterThanOrEqual(3);
  });

  it('tenant child rows use composite (id, org_id) foreign keys', async () => {
    // DISTINCT: FKs on partitioned tables are cloned onto every partition under the same name.
    const rows = await sql<{ conname: string }[]>`
      select distinct conname from pg_constraint
      where connamespace = 'app'::regnamespace and contype = 'f'
        and conname in ('memberships_workspace_org_fk', 'research_jobs_workspace_org_fk',
                        'usage_unit_keys_job_org_fk', 'usage_events_job_org_fk')`;
    expect(rows.map((r) => r.conname).sort()).toEqual([
      'memberships_workspace_org_fk',
      'research_jobs_workspace_org_fk',
      'usage_events_job_org_fk',
      'usage_unit_keys_job_org_fk',
    ]);
  });

  it('billing state is not writable by the API role', async () => {
    const [row] = await sql<{ balance: boolean; plan: boolean; name: boolean; ledger: boolean }[]>`
      select has_column_privilege('app_api', 'app.organizations', 'credits_balance', 'update') as balance,
             has_column_privilege('app_api', 'app.organizations', 'plan', 'update') as plan,
             has_column_privilege('app_api', 'app.organizations', 'name', 'update') as name,
             has_table_privilege('app_api', 'app.credit_ledger', 'insert') as ledger`;
    expect(row).toEqual({ balance: false, plan: false, name: true, ledger: false });
  });

  it('no app role has BYPASSRLS', async () => {
    const rows = await sql<{ rolname: string }[]>`
      select rolname from pg_roles where rolname in ('app_api', 'app_worker') and rolbypassrls`;
    expect(rows).toEqual([]);
  });

  it('SECURITY DEFINER functions are not executable by PUBLIC or app_worker', async () => {
    const rows = await sql<{ proname: string; pub: boolean; worker: boolean }[]>`
      select p.proname,
             has_function_privilege('public', p.oid, 'execute') as pub,
             has_function_privilege('app_worker', p.oid, 'execute') as worker
      from pg_proc p join pg_namespace n on n.oid = p.pronamespace
      where n.nspname = 'app' and p.prosecdef`;
    expect(rows.length).toBeGreaterThanOrEqual(4);
    expect(rows.filter((r) => r.pub || r.worker).map((r) => r.proname)).toEqual([]);
  });

  it('definer functions pin search_path', async () => {
    const rows = await sql<{ proname: string }[]>`
      select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
      where n.nspname = 'app' and p.prosecdef
        and not exists (select 1 from unnest(coalesce(p.proconfig, '{}')) c where c like 'search_path=%')`;
    expect(rows).toEqual([]);
  });

  it('append-only tables reject UPDATE/DELETE from app roles', async () => {
    const rows = await sql<{ relname: string }[]>`
      select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
      cross join (values ('app_api'), ('app_worker')) as r(role)
      where n.nspname = 'app' and c.relname in ('credit_ledger', 'audit_logs', 'usage_events', 'usage_unit_keys')
        and (has_table_privilege(r.role, c.oid, 'update') or has_table_privilege(r.role, c.oid, 'delete'))`;
    expect(rows).toEqual([]);
  });

  it('users cannot grant themselves platform staff', async () => {
    const [row] = await sql<{ ins: boolean; upd: boolean }[]>`
      select has_column_privilege('app_api', 'app.user_profiles', 'is_platform_staff', 'insert') as ins,
             has_column_privilege('app_api', 'app.user_profiles', 'is_platform_staff', 'update') as upd`;
    expect(row).toEqual({ ins: false, upd: false });
  });

  it('migration bookkeeping is outside schema app', async () => {
    const [row] = await sql<{ n: number }[]>`
      select count(*)::int as n from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'app' and c.relname like '%drizzle%'`;
    expect(row?.n).toBe(0);
  });
});
