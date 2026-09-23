/**
 * Phase 1 acceptance: org A cannot read or write org B rows in any tenant table (docs/04 RLS pattern,
 * ADR-0003). Runs as the real app roles against the dev/test database; skipped without
 * DATABASE_URL, DATABASE_URL_WORKERS and DATABASE_URL_MIGRATIONS. Cleans up everything it creates
 * (`pnpm db:sweep-test-data` removes leftovers of crashed runs).
 *
 * Every negative assertion names the exact failure it expects (RLS vs missing privilege) and, where
 * the role may write, is paired with a positive control on its own org, so an unrelated error
 * (syntax, constraint, revoked grant) cannot make a test pass.
 */
import './helpers/to-fail-with.ts';

import postgres from 'postgres';
import { uuidv7 } from 'uuidv7';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import {
  createDb,
  type Database,
  sql,
  type Transaction,
  withTenant,
  withUser,
} from '../src/index.ts';

const RLS = /row-level security/;
const DENIED = /permission denied/;

class Rollback extends Error {}

/** Runs `fn` in a tenant transaction and always rolls back; resolves only if `fn` succeeded. */
async function inRolledBack(
  db: Database,
  context: { orgId: string; userId: string | null },
  fn: (tx: Transaction) => Promise<unknown>,
): Promise<void> {
  try {
    await withTenant(db, context, async (tx) => {
      await fn(tx);
      throw new Rollback();
    });
  } catch (err) {
    if (!(err instanceof Rollback)) throw err;
  }
}

const ownerUrl = process.env.DATABASE_URL_MIGRATIONS;
const apiUrl = process.env.DATABASE_URL;
const workerUrl = process.env.DATABASE_URL_WORKERS;
const live = Boolean(ownerUrl && apiUrl && workerUrl);

/** Every table in schema app must appear here (checked below). */
const TENANT_TABLES = [
  'organizations',
  'workspaces',
  'memberships',
  'research_jobs',
  'credit_ledger',
  'usage_unit_keys',
  'usage_events',
  'audit_logs',
  'job_runs',
  'searches',
  'research_tasks',
] as const;
type TenantTable = (typeof TENANT_TABLES)[number];
const USER_SCOPED_TABLES = ['user_profiles'] as const;
const GLOBAL_TABLES = ['sources'] as const;
/** Shared company graph: both roles read, only app_worker writes (ADR-0007). */
const GRAPH_TABLES = ['companies', 'company_domains', 'company_locations', 'field_values'] as const;
/** Seeded reference data: read-only for both app roles. */
const REFERENCE_TABLES = ['industries', 'geo_areas', 'credit_rates', 'plans'] as const;

interface Org {
  user: string;
  org: string;
  workspace: string;
  job: string;
  search: string;
  task: string;
}

// UUID v7 is time-ordered: anything created by this run sorts after this marker.
const RUN_MARKER = uuidv7();
const newOrg = (): Org => ({
  user: uuidv7(),
  org: uuidv7(),
  workspace: uuidv7(),
  job: uuidv7(),
  search: uuidv7(),
  task: uuidv7(),
});

/** An INSERT into `table` for `target`, written from the point of view of `actor` (the context). */
function insertFor(
  table: Exclude<TenantTable, 'organizations'>,
  target: Org,
  actor: string | null,
  /** A user who is not yet a member of target's workspace (for memberships). */
  newMember: string,
): string {
  const id = uuidv7();
  switch (table) {
    case 'workspaces':
      return `insert into app.workspaces (id, org_id, name) values ('${id}', '${target.org}', 'x')`;
    case 'memberships':
      return `insert into app.memberships (id, org_id, workspace_id, user_id, role) values ('${id}', '${target.org}', '${target.workspace}', '${newMember}', 'viewer')`;
    case 'research_jobs':
      return `insert into app.research_jobs (id, org_id, workspace_id) values ('${id}', '${target.org}', '${target.workspace}')`;
    case 'credit_ledger':
      return `insert into app.credit_ledger (id, org_id, delta, balance_after, reason) values ('${id}', '${target.org}', 1000, 1000, 'grant')`;
    case 'usage_unit_keys':
      return `insert into app.usage_unit_keys (org_id, research_job_id, meter, unit_key, usage_event_id) values ('${target.org}', '${target.job}', 'x', '${id}', '${id}')`;
    case 'usage_events':
      return `insert into app.usage_events (id, org_id, research_job_id, meter, units, unit_key) values ('${id}', '${target.org}', '${target.job}', 'x', 1, 'y')`;
    case 'audit_logs':
      return `insert into app.audit_logs (id, org_id, actor_user_id, action) values ('${id}', '${target.org}', ${actor ? `'${actor}'` : 'null'}, 'test.probe')`;
    case 'job_runs':
      return `insert into app.job_runs (id, org_id, workspace_id, type) values ('${id}', '${target.org}', '${target.workspace}', 'system.ping')`;
    case 'searches':
      return `insert into app.searches (id, org_id, workspace_id, raw_query, spec, spec_version) values ('${id}', '${target.org}', '${target.workspace}', 'q', '{}', 1)`;
    case 'research_tasks':
      return `insert into app.research_tasks (id, org_id, research_job_id, type) values ('${id}', '${target.org}', '${target.job}', 'discovery.probe')`;
  }
}

describe.skipIf(!live)('RLS isolation between organizations', () => {
  const owner = postgres(ownerUrl ?? '', { max: 1, prepare: false, onnotice: () => undefined });
  const apiClient = postgres(apiUrl ?? '', { max: 1, prepare: false });
  const workerClient = postgres(workerUrl ?? '', { max: 1, prepare: false });
  const api: Database = createDb(apiClient);
  const worker: Database = createDb(workerClient);
  const a = newOrg();
  const b = newOrg();
  /** A second member of org A only. */
  const teammate = uuidv7();
  /** A member of both orgs. */
  const consultant = uuidv7();

  async function addUser(id: string, label: string): Promise<void> {
    await owner`insert into auth.users (id, email, aud, role, email_confirmed_at)
                values (${id}, ${`${label}-${id}@test.invalid`}, 'authenticated', 'authenticated', now())`;
    await owner`insert into app.user_profiles (user_id, full_name) values (${id}, ${label})`;
  }

  async function addMember(o: Org, user: string, role: string): Promise<void> {
    await owner`insert into app.memberships (id, org_id, workspace_id, user_id, role)
                values (${uuidv7()}, ${o.org}, ${o.workspace}, ${user}, ${role}::app.membership_role)`;
  }

  async function seed(o: Org, label: string): Promise<void> {
    await addUser(o.user, label);
    await owner`insert into app.organizations (id, name, slug) values (${o.org}, ${label}, ${`rls-${label}-${o.org.slice(-12)}`})`;
    await owner`insert into app.workspaces (id, org_id, name) values (${o.workspace}, ${o.org}, 'Default')`;
    await addMember(o, o.user, 'owner');
    await owner`insert into app.searches (id, org_id, workspace_id, raw_query, spec, spec_version)
                values (${o.search}, ${o.org}, ${o.workspace}, ${`restaurants ${label}`}, '{}', 1)`;
    await owner`insert into app.research_jobs (id, org_id, workspace_id, search_id)
                values (${o.job}, ${o.org}, ${o.workspace}, ${o.search})`;
    await owner`insert into app.research_tasks (id, org_id, research_job_id, type)
                values (${o.task}, ${o.org}, ${o.job}, 'discovery.seed')`;
    await owner`insert into app.credit_ledger (id, org_id, delta, balance_after, reason)
                values (${uuidv7()}, ${o.org}, 50, 50, 'grant')`;
    const usageId = uuidv7();
    await owner`insert into app.usage_events (id, org_id, research_job_id, meter, units, unit_key)
                values (${usageId}, ${o.org}, ${o.job}, 'test', 1, ${`k-${label}`})`;
    await owner`insert into app.usage_unit_keys (org_id, research_job_id, meter, unit_key, usage_event_id)
                values (${o.org}, ${o.job}, 'test', ${`k-${label}`}, ${usageId})`;
    await owner`insert into app.audit_logs (id, org_id, actor_user_id, action)
                values (${uuidv7()}, ${o.org}, ${o.user}, 'test.seeded')`;
    await owner`insert into app.job_runs (id, org_id, workspace_id, type)
                values (${uuidv7()}, ${o.org}, ${o.workspace}, 'system.ping')`;
  }

  beforeAll(async () => {
    await seed(a, 'orga');
    await seed(b, 'orgb');
    await addUser(teammate, 'teammate');
    await addMember(a, teammate, 'member');
    await addUser(consultant, 'consultant');
    await addMember(a, consultant, 'viewer');
    await addMember(b, consultant, 'viewer');
  });

  afterAll(async () => {
    const orgs = [a.org, b.org];
    const users = [a.user, b.user, teammate, consultant];
    try {
      await owner`delete from app.organizations where id = any(${orgs}::uuid[])`;
      await owner`delete from app.audit_logs where actor_user_id = any(${users}::uuid[]) or org_id = any(${orgs}::uuid[])`;
      await owner`delete from auth.users where id = any(${users}::uuid[])`;
    } finally {
      await Promise.all([owner.end(), apiClient.end(), workerClient.end()]);
    }
  });

  it('covers every table in schema app, and all of them (and partitions) force RLS', async () => {
    const rows = await owner<{ relname: string; forced: boolean; child: boolean }[]>`
      select c.relname, (c.relrowsecurity and c.relforcerowsecurity) as forced, c.relispartition as child
      from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'app' and c.relkind in ('r', 'p')`;
    expect(
      rows
        .filter((r) => !r.child)
        .map((r) => r.relname)
        .sort(),
    ).toEqual(
      [
        ...TENANT_TABLES,
        ...USER_SCOPED_TABLES,
        ...GLOBAL_TABLES,
        ...GRAPH_TABLES,
        ...REFERENCE_TABLES,
      ].sort(),
    );
    expect(rows.filter((r) => !r.forced).map((r) => r.relname)).toEqual([]);
  });

  describe.each([
    ['app_api', (): Database => api, (): string | null => a.user],
    ['app_worker', (): Database => worker, (): string | null => null],
  ] as const)('as %s in org A context', (role, db, user) => {
    const ctx = () => ({ orgId: a.org, userId: user() });

    it.each(TENANT_TABLES)('%s: SELECT returns org A rows only', async (table) => {
      const column = table === 'organizations' ? 'id' : 'org_id';
      const rows = await withTenant(db(), ctx(), (tx) =>
        tx.execute<{ owner: string | null }>(
          sql.raw(`select ${column}::text as owner from app.${table}`),
        ),
      );
      expect(rows.length, table).toBeGreaterThan(0);
      expect(
        rows.every((r) => r.owner === a.org),
        table,
      ).toBe(true);
    });

    it.each(TENANT_TABLES.filter((t) => t !== 'organizations'))(
      '%s: INSERT for org B fails for the right reason (with own-org control)',
      async (table) => {
        const [grant] = await owner<{ can: boolean }[]>`
          select has_table_privilege(${role}, ${`app.${table}`}, 'insert') as can`;
        // Started only when awaited: a rejection nobody is listening for yet is reported as an
        // unhandled error (seen in CI, where the local database answers faster than the control).
        const attempt = () =>
          withTenant(db(), ctx(), (tx) => tx.execute(sql.raw(insertFor(table, b, user(), a.user))));
        if (grant?.can === true) {
          // Positive control: the same statement for the own org works.
          await inRolledBack(db(), ctx(), (tx) =>
            tx.execute(sql.raw(insertFor(table, a, user(), b.user))),
          );
          await expect(attempt(), table).toFailWith(RLS);
        } else {
          await expect(attempt(), table).toFailWith(DENIED);
        }
      },
    );

    it.each(['workspaces', 'memberships', 'research_jobs'] as const)(
      '%s: UPDATE cannot move own rows into org B (WITH CHECK)',
      async (table) => {
        const where = {
          workspaces: `id = '${a.workspace}'`,
          memberships: `user_id = '${a.user}'`,
          research_jobs: `id = '${a.job}'`,
        }[table];
        // research_jobs: org_id is not even grantable to the app roles (migration 0015), so the
        // statement is refused before RLS sees it.
        const expected = table === 'research_jobs' ? DENIED : RLS;
        await expect(
          withTenant(db(), ctx(), (tx) =>
            tx.execute(sql.raw(`update app.${table} set org_id = '${b.org}' where ${where}`)),
          ),
          table,
        ).toFailWith(expected);
      },
    );

    it.each(TENANT_TABLES)('%s: UPDATE/DELETE never touch org B rows', async (table) => {
      const [privileges] = await owner<{ upd: boolean; del: boolean; col: string | null }[]>`
        select has_any_column_privilege(${role}, ${`app.${table}`}, 'update') as upd,
               has_table_privilege(${role}, ${`app.${table}`}, 'delete') as del,
               (select a.attname from pg_attribute a
                where a.attrelid = ${`app.${table}`}::regclass and a.attnum > 0 and not a.attisdropped
                  and has_column_privilege(${role}, a.attrelid, a.attnum, 'update')
                order by a.attnum limit 1) as col`;
      const column = table === 'organizations' ? 'id' : 'org_id';
      // Touch a column the role may update (column grants), so the statement reaches RLS.
      const updatable = privileges?.col ?? column;
      const setClause = `${updatable} = ${updatable}`;
      const cases = [
        {
          can: privileges?.upd === true,
          statement: `update app.${table} set ${setClause} where ${column} = '${b.org}' returning 1`,
        },
        {
          can: privileges?.del === true,
          statement: `delete from app.${table} where ${column} = '${b.org}' returning 1`,
        },
      ];
      for (const { can, statement } of cases) {
        const run = withTenant(db(), ctx(), (tx) => tx.execute(sql.raw(statement)));
        if (can) {
          expect(await run, `${table}: ${statement}`).toHaveLength(0);
        } else {
          await expect(run, `${table}: ${statement}`).toFailWith(DENIED);
        }
      }
      const [still] = await owner.unsafe<{ n: number }[]>(
        `select count(*)::int as n from app.${table} where ${column} = $1`,
        [b.org],
      );
      expect(still?.n, table).toBeGreaterThan(0);
    });

    it('cannot read any partition child directly', async () => {
      const children = await owner<{ relname: string }[]>`
        select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'app' and c.relispartition and c.relkind = 'r'`;
      expect(children.length).toBeGreaterThan(0);
      for (const child of children) {
        await expect(
          withTenant(db(), ctx(), (tx) =>
            tx.execute(sql.raw(`select 1 from app.${child.relname} limit 1`)),
          ),
          child.relname,
        ).toFailWith(DENIED);
      }
    });
  });

  it('without any context both roles see no tenant rows', async () => {
    for (const client of [apiClient, workerClient]) {
      const [row] = await client<{ jobs: number; ledger: number; usage: number }[]>`
        select (select count(*) from app.research_jobs)::int as jobs,
               (select count(*) from app.credit_ledger)::int as ledger,
               (select count(*) from app.usage_events)::int as usage`;
      expect(row).toEqual({ jobs: 0, ledger: 0, usage: 0 });
    }
  });

  it('context does not leak to the next statement on the same pooled connection', async () => {
    await withTenant(worker, { orgId: a.org }, (tx) =>
      tx.execute(sql`select 1 from app.research_jobs`),
    );
    const [row] = await workerClient<{ jobs: number; org: string | null }[]>`
      select (select count(*) from app.research_jobs)::int as jobs, app.current_org_id()::text as org`;
    expect(row).toEqual({ jobs: 0, org: null });
  });

  it('org context cannot link its rows to another org (composite FKs)', async () => {
    const attempts = [
      `insert into app.memberships (id, org_id, workspace_id, user_id, role) values ('${uuidv7()}', '${a.org}', '${b.workspace}', '${a.user}', 'owner')`,
      `insert into app.research_jobs (id, org_id, workspace_id) values ('${uuidv7()}', '${a.org}', '${b.workspace}')`,
      `insert into app.usage_events (id, org_id, research_job_id, meter, units, unit_key) values ('${uuidv7()}', '${a.org}', '${b.job}', 'x', 1, 'y')`,
      `insert into app.usage_unit_keys (org_id, research_job_id, meter, unit_key, usage_event_id) values ('${a.org}', '${b.job}', 'x', 'y', '${uuidv7()}')`,
    ];
    for (const statement of attempts) {
      await expect(
        withTenant(worker, { orgId: a.org }, (tx) => tx.execute(sql.raw(statement))),
        statement,
      ).toFailWith(/foreign key/);
    }
  });

  it('phase 2 tables cannot link to another org or another job (composite FKs)', async () => {
    const otherJob = uuidv7();
    const otherTask = uuidv7();
    await owner`insert into app.research_jobs (id, org_id, workspace_id) values (${otherJob}, ${a.org}, ${a.workspace})`;
    await owner`insert into app.research_tasks (id, org_id, research_job_id, type)
                values (${otherTask}, ${a.org}, ${otherJob}, 'discovery.other')`;
    const attempts: [Database, string][] = [
      // API-created rows (the API holds INSERT on these).
      [
        api,
        `insert into app.searches (id, org_id, workspace_id, raw_query, spec, spec_version) values ('${uuidv7()}', '${a.org}', '${b.workspace}', 'q', '{}', 1)`,
      ],
      [
        api,
        `insert into app.research_jobs (id, org_id, workspace_id, search_id) values ('${uuidv7()}', '${a.org}', '${a.workspace}', '${b.search}')`,
      ],
      // Worker-created tasks: job of org B, parent task of org B, parent task of another job.
      [
        worker,
        `insert into app.research_tasks (id, org_id, research_job_id, type) values ('${uuidv7()}', '${a.org}', '${b.job}', 'discovery.x')`,
      ],
      [
        worker,
        `insert into app.research_tasks (id, org_id, research_job_id, parent_task_id, type) values ('${uuidv7()}', '${a.org}', '${a.job}', '${b.task}', 'discovery.x')`,
      ],
      [
        worker,
        `insert into app.research_tasks (id, org_id, research_job_id, parent_task_id, type) values ('${uuidv7()}', '${a.org}', '${a.job}', '${otherTask}', 'discovery.x')`,
      ],
    ];
    for (const [db, statement] of attempts) {
      await expect(
        withTenant(db, { orgId: a.org, userId: db === api ? a.user : null }, (tx) =>
          tx.execute(sql.raw(statement)),
        ),
        statement,
      ).toFailWith(/foreign key/);
    }
    // Positive control: a child of a task in the same job works.
    await inRolledBack(worker, { orgId: a.org, userId: null }, (tx) =>
      tx.execute(
        sql.raw(
          `insert into app.research_tasks (id, org_id, research_job_id, parent_task_id, type) values ('${uuidv7()}', '${a.org}', '${a.job}', '${a.task}', 'discovery.x')`,
        ),
      ),
    );
  });

  it('research_tasks: workers update run state only; the API only reads', async () => {
    const workerCtx = { orgId: a.org, userId: null };
    await inRolledBack(worker, workerCtx, (tx) =>
      tx.execute(
        sql`update app.research_tasks set status = 'running', attempts = 1 where id = ${a.task}`,
      ),
    );
    for (const column of ['input', 'credit_budget', 'type', 'research_job_id', 'org_id']) {
      await expect(
        withTenant(worker, workerCtx, (tx) =>
          tx.execute(
            sql.raw(`update app.research_tasks set ${column} = ${column} where id = '${a.task}'`),
          ),
        ),
        column,
      ).toFailWith(DENIED);
    }
    await expect(
      withTenant(api, { orgId: a.org, userId: a.user }, (tx) =>
        tx.execute(sql`update app.research_tasks set status = 'cancelled' where id = ${a.task}`),
      ),
    ).toFailWith(DENIED);
    await expect(
      withTenant(api, { orgId: a.org, userId: a.user }, (tx) =>
        tx.execute(
          sql.raw(
            `insert into app.research_tasks (id, org_id, research_job_id, type) values ('${uuidv7()}', '${a.org}', '${a.job}', 'discovery.x')`,
          ),
        ),
      ),
    ).toFailWith(DENIED);
  });

  it('a user context without an org sees only own memberships and orgs, never teammates', async () => {
    const result = await withUser(api, a.user, async (tx) => ({
      jobs: await tx.execute(sql`select 1 from app.research_jobs`),
      memberships: await tx.execute<{ user_id: string }>(
        sql`select user_id::text from app.memberships`,
      ),
      orgs: await tx.execute<{ id: string }>(sql`select id::text from app.organizations`),
    }));
    expect(result.jobs).toHaveLength(0);
    expect(result.memberships.map((m) => m.user_id)).toEqual([a.user]);
    expect(result.orgs.map((o) => o.id)).toEqual([a.org]);
  });

  it('a two-org member sees the other org name but none of its tenant data in org A context', async () => {
    const seen = await withTenant(api, { orgId: a.org, userId: consultant }, async (tx) => ({
      orgs: await tx.execute<{ id: string }>(sql`select id::text from app.organizations`),
      jobs: await tx.execute<{ org_id: string }>(sql`select org_id::text from app.research_jobs`),
      ledger: await tx.execute<{ org_id: string }>(sql`select org_id::text from app.credit_ledger`),
    }));
    // By design (ADR-0003): names of orgs you belong to stay visible (workspace switcher).
    expect(seen.orgs.map((o) => o.id).sort()).toEqual([a.org, b.org].sort());
    expect(new Set(seen.jobs.map((j) => j.org_id))).toEqual(new Set([a.org]));
    expect(new Set(seen.ledger.map((l) => l.org_id))).toEqual(new Set([a.org]));
  });

  it('withTenant refuses a user context for a foreign org', async () => {
    await expect(
      withTenant(api, { orgId: b.org, userId: a.user }, () => Promise.resolve(1)),
    ).toFailWith(/not a member/);
  });

  it('user_profiles: own row only, and the staff flag cannot be self-granted', async () => {
    const rows = await withUser(api, a.user, (tx) =>
      tx.execute<{ user_id: string }>(sql`select user_id::text from app.user_profiles`),
    );
    expect(rows.map((r) => r.user_id)).toEqual([a.user]);
    await expect(
      withUser(api, a.user, (tx) =>
        tx.execute(
          sql`update app.user_profiles set is_platform_staff = true where user_id = ${a.user}`,
        ),
      ),
    ).toFailWith(DENIED);
    const renamed = await withUser(api, a.user, (tx) =>
      tx.execute(
        sql`update app.user_profiles set full_name = 'x' where user_id = ${b.user} returning 1`,
      ),
    );
    expect(renamed).toHaveLength(0);
  });

  it('audit rows cannot claim another actor (own actor works)', async () => {
    const insert = (actor: string) =>
      sql`insert into app.audit_logs (id, org_id, actor_user_id, action) values (${uuidv7()}, ${a.org}, ${actor}, 'test.probe')`;
    await inRolledBack(api, { orgId: a.org, userId: a.user }, (tx) => tx.execute(insert(a.user)));
    await expect(
      withTenant(api, { orgId: a.org, userId: a.user }, (tx) => tx.execute(insert(teammate))),
    ).toFailWith(RLS);
  });

  it('privileged functions: admin needs staff, workers cannot call them, bootstrap checks the caller', async () => {
    await expect(
      withUser(api, a.user, (tx) =>
        tx.execute(sql`select * from app.admin_list_orgs(${uuidv7()}::uuid)`),
      ),
    ).toFailWith(/platform staff only/);
    await expect(
      withTenant(worker, { orgId: a.org }, (tx) =>
        tx.execute(sql`select * from app.admin_list_users(${uuidv7()}::uuid)`),
      ),
    ).toFailWith(DENIED);
    await expect(
      withUser(api, a.user, (tx) =>
        tx.execute(sql`select app.bootstrap_org(${b.user}::uuid, ${uuidv7()}::uuid, 'x', ${`x-${uuidv7().slice(-8)}`},
          ${uuidv7()}::uuid, 'x', ${uuidv7()}::uuid, ${uuidv7()}::uuid, ${uuidv7()}::uuid)`),
      ),
    ).toFailWith(/caller does not match/);
  });

  it('platform staff can list orgs through the audited admin function', async () => {
    await owner`update app.user_profiles set is_platform_staff = true where user_id = ${a.user}`;
    try {
      const rows = await withUser(api, a.user, (tx) =>
        tx.execute<{ id: string }>(
          sql`select id::text from app.admin_list_orgs(${uuidv7()}::uuid, 200, ${RUN_MARKER}::uuid)`,
        ),
      );
      expect(rows.map((r) => r.id)).toEqual(expect.arrayContaining([a.org, b.org]));
      const [audit] = await owner<{ n: number }[]>`
        select count(*)::int as n from app.audit_logs where actor_user_id = ${a.user} and action = 'admin.orgs.listed'`;
      expect(audit?.n).toBeGreaterThan(0);
    } finally {
      await owner`update app.user_profiles set is_platform_staff = false where user_id = ${a.user}`;
    }
  });

  it('global sources are readable by both roles but not writable by the API', async () => {
    const rows = await withTenant(api, { orgId: a.org, userId: a.user }, (tx) =>
      tx.execute(sql`select key from app.sources`),
    );
    expect(rows.length).toBeGreaterThan(0);
    await expect(
      withTenant(api, { orgId: a.org, userId: a.user }, (tx) =>
        tx.execute(sql`update app.sources set enabled = true`),
      ),
    ).toFailWith(DENIED);
  });
  describe('shared company graph and reference data (ADR-0007)', () => {
    const company = uuidv7();
    const googleSource = async () => {
      const [row] = await owner<
        { id: string }[]
      >`select id from app.sources where key = 'google_places'`;
      if (!row) throw new Error('sources not seeded');
      return row.id;
    };
    const graphInsert = (table: (typeof GRAPH_TABLES)[number], sourceId: string) => {
      const id = uuidv7();
      switch (table) {
        case 'companies':
          return `insert into app.companies (id, canonical_name, normalized_name) values ('${id}', 'Probe', 'probe')`;
        case 'company_domains':
          return `insert into app.company_domains (id, company_id, domain) values ('${id}', '${company}', 'probe-${id}.example')`;
        case 'company_locations':
          return `insert into app.company_locations (id, company_id, city) values ('${id}', '${company}', 'Delhi')`;
        case 'field_values':
          return `insert into app.field_values (id, entity_type, entity_id, field, value, source_id, source_url, method, confidence, observed_at) values ('${id}', 'company', '${company}', 'name', '"Probe"', '${sourceId}', 'https://maps.google.com/?cid=1', 'api', 0.9, now())`;
      }
    };

    beforeAll(async () => {
      await owner`insert into app.companies (id, canonical_name, normalized_name) values (${company}, 'Probe Co', 'probe co')`;
    });
    afterAll(async () => {
      await owner`delete from app.field_values where entity_id = ${company}`;
      await owner`delete from app.companies where id = ${company}`;
    });

    it.each(GRAPH_TABLES)('%s: API reads but cannot write; workers can', async (table) => {
      const sourceId = await googleSource();
      await withTenant(api, { orgId: a.org, userId: a.user }, (tx) =>
        tx.execute(sql.raw(`select 1 from app.${table} limit 1`)),
      );
      await expect(
        withTenant(api, { orgId: a.org, userId: a.user }, (tx) =>
          tx.execute(sql.raw(graphInsert(table, sourceId))),
        ),
        table,
      ).toFailWith(DENIED);
      await inRolledBack(worker, { orgId: a.org, userId: null }, (tx) =>
        tx.execute(sql.raw(graphInsert(table, sourceId))),
      );
    });

    it('graph rows are visible from every org (shared, not tenant-scoped)', async () => {
      for (const o of [a, b]) {
        const rows = await withTenant(api, { orgId: o.org, userId: o.user }, (tx) =>
          tx.execute(sql`select id from app.companies where id = ${company}`),
        );
        expect(rows).toHaveLength(1);
      }
    });

    it('field_values: workers cannot rewrite history, only flip is_current', async () => {
      await expect(
        withTenant(worker, { orgId: a.org, userId: null }, (tx) =>
          tx.execute(sql`update app.field_values set value = '"x"' where entity_id = ${company}`),
        ),
      ).toFailWith(DENIED);
      await inRolledBack(worker, { orgId: a.org, userId: null }, (tx) =>
        tx.execute(
          sql`update app.field_values set is_current = false where entity_id = ${company}`,
        ),
      );
      await expect(
        withTenant(worker, { orgId: a.org, userId: null }, (tx) =>
          tx.execute(sql`delete from app.field_values where entity_id = ${company}`),
        ),
      ).toFailWith(DENIED);
    });

    it('field_values: database guards for AI honesty and provenance', async () => {
      const sourceId = await googleSource();
      const insert = (
        field: string,
        method: string,
        extra: { model?: string; url?: string } = {},
      ) =>
        withTenant(worker, { orgId: a.org, userId: null }, (tx) =>
          tx.execute(sql`insert into app.field_values
                           (id, entity_type, entity_id, field, value, source_id, source_url, method, confidence, observed_at, model, prompt_version)
                         values (${uuidv7()}, 'company', ${company}, ${field}, '"x"', ${sourceId},
                                 ${extra.url ?? 'https://x.test'}, ${method}::app.value_method, 0.5, now(),
                                 ${extra.model ?? null}, ${extra.model ? 'v1' : null})`),
        );
      await expect(insert('summary', 'ai')).toFailWith(/field_values_ai_traceable/);
      await expect(insert('email', 'ai', { model: 'm' })).toFailWith(/field_values_ai_no_contacts/);
      await expect(insert('phone', 'ai', { model: 'm' })).toFailWith(/field_values_ai_no_contacts/);
      await expect(insert('name', 'api', { url: 'ftp://x' })).toFailWith(
        /field_values_source_url_format/,
      );
      await inRolledBack(worker, { orgId: a.org, userId: null }, (tx) =>
        tx.execute(sql`insert into app.field_values
                         (id, entity_type, entity_id, field, value, source_id, source_url, method, confidence, observed_at, model, prompt_version)
                       values (${uuidv7()}, 'company', ${company}, 'summary', '"x"', ${sourceId}, 'https://x.test', 'ai', 0.5, now(), 'm', 'v1')`),
      );
    });

    it('graph: check constraints keep upsert keys canonical', async () => {
      const bad = [
        `insert into app.companies (id, canonical_name, normalized_name, primary_domain) values ('${uuidv7()}', 'x', 'x', 'www.probe.example')`,
        `insert into app.companies (id, canonical_name, normalized_name, primary_domain) values ('${uuidv7()}', 'x', 'x', 'Probe.example')`,
        `insert into app.companies (id, canonical_name, normalized_name, primary_domain) values ('${uuidv7()}', 'x', 'x', 'https://probe.example/about')`,
        `insert into app.company_locations (id, company_id, phone_e164) values ('${uuidv7()}', '${company}', '011 2345 6789')`,
        `insert into app.company_locations (id, company_id, country) values ('${uuidv7()}', '${company}', 'in')`,
      ];
      for (const statement of bad) {
        await expect(
          withTenant(worker, { orgId: a.org, userId: null }, (tx) =>
            tx.execute(sql.raw(statement)),
          ),
          statement,
        ).toFailWith(/check constraint/);
      }
    });

    it('graph: workers upsert locations by google_place_id', async () => {
      const place = `probe-place-${uuidv7()}`;
      await inRolledBack(worker, { orgId: a.org, userId: null }, async (tx) => {
        for (const phone of ['+911123456789', '+911198765432']) {
          await tx.execute(sql`insert into app.company_locations (id, company_id, google_place_id, phone_e164)
                               values (${uuidv7()}, ${company}, ${place}, ${phone})
                               on conflict (google_place_id) do update set phone_e164 = excluded.phone_e164`);
        }
        const rows = await tx.execute<{ phone_e164: string }>(
          sql`select phone_e164 from app.company_locations where google_place_id = ${place}`,
        );
        expect(rows.map((r) => r.phone_e164)).toEqual(['+911198765432']);
      });
    });

    it.each(REFERENCE_TABLES)('%s: readable, not writable by either role', async (table) => {
      for (const db of [api, worker]) {
        await withTenant(db, { orgId: a.org, userId: null }, (tx) =>
          tx.execute(sql.raw(`select 1 from app.${table} limit 1`)),
        );
        await expect(
          withTenant(db, { orgId: a.org, userId: null }, (tx) =>
            tx.execute(sql.raw(`delete from app.${table}`)),
          ),
          table,
        ).toFailWith(DENIED);
      }
    });
  });
});
