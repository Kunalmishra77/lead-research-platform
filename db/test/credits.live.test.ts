/**
 * Credit ledger operations against the database (Phase 2 task 2.5, docs/11):
 * signup grant, reserve, settle. Skipped without DATABASE_URL + DATABASE_URL_MIGRATIONS.
 */
import './helpers/to-fail-with.ts';

import { createDb, type Database, sql, withTenant, withUser } from '@leadforge/db';
import postgres from 'postgres';
import { uuidv7 } from 'uuidv7';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const ownerUrl = process.env.DATABASE_URL_MIGRATIONS;
const apiUrl = process.env.DATABASE_URL;
const live = Boolean(ownerUrl && apiUrl);

const INSUFFICIENT = /insufficient credits/;
const DENIED = /permission denied/;

describe.skipIf(!live)('credit ledger operations', () => {
  const owner = postgres(ownerUrl ?? '', { max: 1, prepare: false, onnotice: () => undefined });
  const apiClient = postgres(apiUrl ?? '', { max: 1, prepare: false });
  const api: Database = createDb(apiClient);
  const user = uuidv7();
  const other = { user: uuidv7(), org: uuidv7(), workspace: uuidv7(), job: uuidv7() };
  let org = '';
  let workspace = '';

  /** postgres.js returns bigint columns as strings; the ledger is small enough for Number. */
  const balance = async (orgId = org): Promise<number> => {
    const [row] = await owner<{ credits_balance: string }[]>`
      select credits_balance from app.organizations where id = ${orgId}`;
    return Number(row?.credits_balance ?? -1);
  };
  const ledger = async (jobId: string) =>
    owner<{ reason: string; delta: number }[]>`
      select reason, delta::int from app.credit_ledger
      where ref_id = ${jobId} order by reason`; // enum order: reserve, consume, release
  /** A job of the active org, created by the owner role (the API creates jobs in task 2.6). */
  const newJob = async (orgId = org, workspaceId = workspace): Promise<string> => {
    const id = uuidv7();
    await owner`insert into app.research_jobs (id, org_id, workspace_id, depth)
                values (${id}, ${orgId}, ${workspaceId}, 'standard')`;
    return id;
  };
  const deliver = async (jobId: string, orgId: string, credits: number, unitKey: string) => {
    const eventId = uuidv7();
    await owner`insert into app.usage_events (id, org_id, research_job_id, meter, units, credits, unit_key)
                values (${eventId}, ${orgId}, ${jobId}, 'research_standard', 1, ${credits}, ${unitKey})`;
    await owner`insert into app.usage_unit_keys (org_id, research_job_id, meter, unit_key, usage_event_id)
                values (${orgId}, ${jobId}, 'research_standard', ${unitKey}, ${eventId})`;
  };

  beforeAll(async () => {
    for (const [id, label] of [
      [user, 'credits-owner'],
      [other.user, 'credits-other'],
    ] as const) {
      await owner`insert into auth.users (id, email, aud, role, email_confirmed_at)
                  values (${id}, ${`${label}-${id}@test.invalid`}, 'authenticated', 'authenticated', now())`;
    }
    // A second org that this user is not a member of (cross-org checks).
    await owner`insert into app.organizations (id, name, slug) values (${other.org}, 'Other', ${`other-${other.org.slice(-12)}`})`;
    await owner`insert into app.workspaces (id, org_id, name) values (${other.workspace}, ${other.org}, 'Default')`;
    await owner`insert into app.memberships (id, org_id, workspace_id, user_id, role)
                values (${uuidv7()}, ${other.org}, ${other.workspace}, ${other.user}, 'owner')`;
    await owner`insert into app.research_jobs (id, org_id, workspace_id) values (${other.job}, ${other.org}, ${other.workspace})`;
  });

  afterAll(async () => {
    const orgs = [org, other.org].filter(Boolean);
    try {
      await owner`delete from app.organizations where id = any(${orgs}::uuid[])`;
      await owner`delete from app.audit_logs where actor_user_id = any(${[user, other.user]}::uuid[])`;
      await owner`delete from auth.users where id = any(${[user, other.user]}::uuid[])`;
    } finally {
      await Promise.all([owner.end(), apiClient.end()]);
    }
  });

  it('signup grants the free plan credits through the ledger', async () => {
    const ids = {
      org: uuidv7(),
      workspace: uuidv7(),
      membership: uuidv7(),
      audit: uuidv7(),
      grant: uuidv7(),
    };
    await withUser(api, user, (tx) =>
      tx.execute(sql`select app.bootstrap_org(${user}::uuid, ${ids.org}::uuid, 'Credits Co',
        ${`credits-${ids.org.slice(-12)}`}, ${ids.workspace}::uuid, 'Default',
        ${ids.membership}::uuid, ${ids.audit}::uuid, ${ids.grant}::uuid)`),
    );
    org = ids.org;
    workspace = ids.workspace;
    const [signup] = await owner<{ signup_credits: number }[]>`
      select signup_credits from app.plans where plan = 'free'`;
    expect(await balance()).toBe(Number(signup?.signup_credits));
    const [row] = await owner<{ reason: string; delta: number; balance_after: number }[]>`
      select reason, delta::int, balance_after::int from app.credit_ledger where id = ${ids.grant}`;
    expect(row).toMatchObject({ reason: 'grant', delta: Number(signup?.signup_credits) });
  });

  it('reserve debits the balance once and is idempotent per job', async () => {
    const job = await newJob();
    const before = await balance();
    const ledgerId = uuidv7();
    await withTenant(api, { orgId: org, userId: user }, (tx) =>
      tx.execute(sql`select app.credit_reserve(${job}::uuid, 20, ${ledgerId}::uuid)`),
    );
    expect(await balance()).toBe(before - 20);
    // A retry of the same request must not debit twice.
    await withTenant(api, { orgId: org, userId: user }, (tx) =>
      tx.execute(sql`select app.credit_reserve(${job}::uuid, 20, ${uuidv7()}::uuid)`),
    );
    expect(await balance()).toBe(before - 20);
    const [job_] = await owner<{ credits_reserved: number; credit_budget: number }[]>`
      select credits_reserved, credit_budget from app.research_jobs where id = ${job}`;
    expect(job_).toMatchObject({ credits_reserved: 20, credit_budget: 20 });
  });

  it('reserve refuses more than the balance (402) and leaves nothing behind', async () => {
    const job = await newJob();
    const before = await balance();
    await expect(
      withTenant(api, { orgId: org, userId: user }, (tx) =>
        tx.execute(sql`select app.credit_reserve(${job}::uuid, ${before + 1}, ${uuidv7()}::uuid)`),
      ),
    ).toFailWith(INSUFFICIENT);
    expect(await balance()).toBe(before);
    expect(await ledger(job)).toEqual([]);
  });

  it('reserve and settle refuse a job of another org', async () => {
    for (const statement of [
      sql`select app.credit_reserve(${other.job}::uuid, 1, ${uuidv7()}::uuid)`,
      sql`select * from app.credit_settle(${other.job}::uuid, ${uuidv7()}::uuid, ${uuidv7()}::uuid)`,
    ]) {
      await expect(
        withTenant(api, { orgId: org, userId: user }, (tx) => tx.execute(statement)),
      ).toFailWith(/not in the active org/);
    }
    expect(await balance(other.org)).toBe(0);
  });

  it('settle books what was delivered and returns the rest', async () => {
    const job = await newJob();
    const before = await balance();
    await withTenant(api, { orgId: org, userId: user }, (tx) =>
      tx.execute(sql`select app.credit_reserve(${job}::uuid, 12, ${uuidv7()}::uuid)`),
    );
    await deliver(job, org, 3, 'lead-1');
    await deliver(job, org, 3, 'lead-2');
    const settled = await withTenant(api, { orgId: org, userId: user }, (tx) =>
      tx.execute<{ consumed: string; released: string }>(
        sql`select * from app.credit_settle(${job}::uuid, ${uuidv7()}::uuid, ${uuidv7()}::uuid)`,
      ),
    );
    expect(settled[0]).toMatchObject({ consumed: '6', released: '6' });
    expect(await balance()).toBe(before - 6);
    // reserve -12, release +12, consume -6 => the job cost exactly what it delivered.
    expect(await ledger(job)).toEqual([
      { reason: 'reserve', delta: -12 },
      { reason: 'consume', delta: -6 },
      { reason: 'release', delta: 12 },
    ]);
    const [row] = await owner<{ credits_used: number; credits_reserved: number }[]>`
      select credits_used, credits_reserved from app.research_jobs where id = ${job}`;
    expect(row).toMatchObject({ credits_used: 6, credits_reserved: 0 });
  });

  it('settle never charges beyond the reservation and runs once', async () => {
    const job = await newJob();
    const before = await balance();
    await withTenant(api, { orgId: org, userId: user }, (tx) =>
      tx.execute(sql`select app.credit_reserve(${job}::uuid, 5, ${uuidv7()}::uuid)`),
    );
    // Over-delivery (parallel tasks racing the budget check) must not overcharge.
    for (const key of ['a', 'b', 'c']) await deliver(job, org, 3, key);
    const settle = () =>
      withTenant(api, { orgId: org, userId: user }, (tx) =>
        tx.execute<{ consumed: string; released: string }>(
          sql`select * from app.credit_settle(${job}::uuid, ${uuidv7()}::uuid, ${uuidv7()}::uuid)`,
        ),
      );
    expect((await settle())[0]).toMatchObject({ consumed: '5', released: '0' });
    expect(await balance()).toBe(before - 5);
    // A second settle is a no-op.
    expect((await settle())[0]).toMatchObject({ consumed: '0', released: '0' });
    expect(await balance()).toBe(before - 5);
  });

  it('settle refuses a job that never reserved', async () => {
    const job = await newJob();
    await expect(
      withTenant(api, { orgId: org, userId: user }, (tx) =>
        tx.execute(
          sql`select * from app.credit_settle(${job}::uuid, ${uuidv7()}::uuid, ${uuidv7()}::uuid)`,
        ),
      ),
    ).toFailWith(/no reservation/);
  });

  it('the API role cannot inflate a reservation through the job row', async () => {
    const job = await newJob();
    await withTenant(api, { orgId: org, userId: user }, (tx) =>
      tx.execute(sql`select app.credit_reserve(${job}::uuid, 5, ${uuidv7()}::uuid)`),
    );
    // Credit columns are not writable: a tampered reservation cannot mint credits on settle.
    for (const column of ['credits_reserved', 'credit_budget', 'credits_used', 'settled_at']) {
      await expect(
        withTenant(api, { orgId: org, userId: user }, (tx) =>
          tx.execute(
            sql.raw(`update app.research_jobs set ${column} = ${column} where id = '${job}'`),
          ),
        ),
        column,
      ).toFailWith(DENIED);
    }
    // Even with a tampered column (owner role), settle releases only what the ledger reserved.
    await owner`update app.research_jobs set credits_reserved = 1000000 where id = ${job}`;
    const before = await balance();
    await withTenant(api, { orgId: org, userId: user }, (tx) =>
      tx.execute(
        sql`select * from app.credit_settle(${job}::uuid, ${uuidv7()}::uuid, ${uuidv7()}::uuid)`,
      ),
    );
    expect(await balance()).toBe(before + 5);
  });

  it('usage recorded after a settle is booked once, never twice and never free', async () => {
    const job = await newJob();
    const before = await balance();
    await withTenant(api, { orgId: org, userId: user }, (tx) =>
      tx.execute(sql`select app.credit_reserve(${job}::uuid, 10, ${uuidv7()}::uuid)`),
    );
    await deliver(job, org, 4, 'early');
    const settle = () =>
      withTenant(api, { orgId: org, userId: user }, (tx) =>
        tx.execute<{ consumed: string; released: string }>(
          sql`select * from app.credit_settle(${job}::uuid, ${uuidv7()}::uuid, ${uuidv7()}::uuid)`,
        ),
      );
    expect((await settle())[0]).toMatchObject({ consumed: '4', released: '6' });
    expect(await balance()).toBe(before - 4);
    // A worker that finished late still gets billed, up to the reservation.
    await deliver(job, org, 3, 'late');
    expect((await settle())[0]).toMatchObject({ consumed: '3', released: '0' });
    expect(await balance()).toBe(before - 7);
    // Nothing new: no further charge.
    expect((await settle())[0]).toMatchObject({ consumed: '0', released: '0' });
    expect(await balance()).toBe(before - 7);
  });

  it('two jobs reserving at once cannot both spend the same balance', async () => {
    const [jobA, jobB] = [await newJob(), await newJob()];
    const available = await balance();
    const each = Math.floor(available * 0.75);
    const attempts = await Promise.allSettled([
      withTenant(api, { orgId: org, userId: user }, (tx) =>
        tx.execute(sql`select app.credit_reserve(${jobA}::uuid, ${each}, ${uuidv7()}::uuid)`),
      ),
      withTenant(api, { orgId: org, userId: user }, (tx) =>
        tx.execute(sql`select app.credit_reserve(${jobB}::uuid, ${each}, ${uuidv7()}::uuid)`),
      ),
    ]);
    expect(attempts.filter((a) => a.status === 'fulfilled')).toHaveLength(1);
    expect(await balance()).toBe(available - each);
  });

  it('the ledger explains the balance exactly', async () => {
    const [row] = await owner<{ ledger: string; cached: string }[]>`
      select (select coalesce(sum(delta), 0) from app.credit_ledger where org_id = ${org}) as ledger,
             (select credits_balance from app.organizations where id = ${org}) as cached`;
    expect(row?.ledger).toBe(row?.cached);
  });

  it('the API role cannot post to the ledger or move the balance itself', async () => {
    await expect(
      withTenant(api, { orgId: org, userId: user }, (tx) =>
        tx.execute(sql`insert into app.credit_ledger (id, org_id, delta, balance_after, reason)
                       values (${uuidv7()}, ${org}, 1000, 1000, 'grant')`),
      ),
    ).toFailWith(/permission denied/);
    await expect(
      withTenant(api, { orgId: org, userId: user }, (tx) =>
        tx.execute(sql`update app.organizations set credits_balance = 999999 where id = ${org}`),
      ),
    ).toFailWith(/permission denied/);
    await expect(
      withTenant(api, { orgId: org, userId: user }, (tx) =>
        tx.execute(
          sql`select app.credit_post(${uuidv7()}::uuid, ${org}::uuid, 10, 'grant', null, null, null)`,
        ),
      ),
    ).toFailWith(/permission denied/);
  });
});
