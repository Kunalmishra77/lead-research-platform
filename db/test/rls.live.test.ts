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

/** Drizzle wraps driver errors ("Failed query: ..."); assertions look at the root cause. */
function rootMessage(err: unknown): string {
  let e: unknown = err;
  while (e instanceof Error && e.cause !== undefined) e = e.cause;
  return e instanceof Error ? e.message : String(e);
}

expect.extend({
  async toFailWith(received: Promise<unknown>, pattern: RegExp) {
    try {
      await received;
    } catch (err) {
      const message = rootMessage(err);
      return {
        pass: pattern.test(message),
        message: () => `expected failure matching ${String(pattern)}, got: ${message}`,
      };
    }
    return {
      pass: false,
      message: () => `expected failure matching ${String(pattern)}, but it succeeded`,
    };
  },
});

declare module 'vitest' {
  // Must repeat Vitest 5's exact type parameters to merge.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface Matchers<R extends void | Promise<void> = void | Promise<void>, T = unknown> {
    toFailWith(pattern: RegExp): Promise<void>;
  }
}

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
] as const;
type TenantTable = (typeof TENANT_TABLES)[number];
const USER_SCOPED_TABLES = ['user_profiles'] as const;
const GLOBAL_TABLES = ['sources'] as const;

interface Org {
  user: string;
  org: string;
  workspace: string;
  job: string;
}

// UUID v7 is time-ordered: anything created by this run sorts after this marker.
const RUN_MARKER = uuidv7();
const newOrg = (): Org => ({ user: uuidv7(), org: uuidv7(), workspace: uuidv7(), job: uuidv7() });

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
    await owner`insert into app.research_jobs (id, org_id, workspace_id) values (${o.job}, ${o.org}, ${o.workspace})`;
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
    ).toEqual([...TENANT_TABLES, ...USER_SCOPED_TABLES, ...GLOBAL_TABLES].sort());
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
        await expect(
          withTenant(db(), ctx(), (tx) =>
            tx.execute(sql.raw(`update app.${table} set org_id = '${b.org}' where ${where}`)),
          ),
          table,
        ).toFailWith(RLS);
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
          ${uuidv7()}::uuid, 'x', ${uuidv7()}::uuid, ${uuidv7()}::uuid)`),
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
});
