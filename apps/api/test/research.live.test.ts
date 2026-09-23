/**
 * Research job lifecycle against the dev/test database and Redis (Phase 2 task 2.6):
 * create (reserve credits), status, history, cancel (release), RBAC and spec validation.
 * Skipped without DATABASE_URL + DATABASE_URL_MIGRATIONS (+ REDIS_URL for the publish path).
 */
import type { ResearchSpec } from '@leadforge/contracts';
import type { NestFastifyApplication } from '@nestjs/platform-fastify';
import { Redis } from 'ioredis';
import postgres from 'postgres';
import { uuidv7 } from 'uuidv7';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { createTestApp } from './helpers/test-app';
import { createTestKeys, type TestKeys } from './helpers/tokens';

const ownerUrl = process.env.DATABASE_URL_MIGRATIONS;
const live = Boolean(ownerUrl && process.env.DATABASE_URL && process.env.REDIS_URL);

const SPEC: ResearchSpec = {
  spec_version: 1,
  entity: 'company',
  intent: 'prospecting',
  filters: {
    industry: { include: ['restaurant'], exclude: [], taxonomy_ids: ['restaurant'] },
    location: {
      country: 'IN',
      states: [],
      cities: ['Delhi'],
      radius_km: null,
      include_metro_area: true,
    },
    has_website: true,
  },
  fields: ['name', 'website', 'phone'],
  depth: 'standard',
  limits: { max_results: 10, max_credits: 100 },
};

describe.skipIf(!live)('research lifecycle against the database', () => {
  const owner = postgres(ownerUrl ?? '', { max: 1, prepare: false, onnotice: () => undefined });
  const redis = new Redis(process.env.REDIS_URL ?? '');
  /** Planning jobs land here until the planner exists (task 2.10); the test cleans them up. */
  const stream = `jobs:${process.env.JOBS_DISCOVERY_POOL ?? 'discovery'}`;
  const users = { owner: uuidv7(), viewer: uuidv7() };
  const sessions: Record<string, string> = { [users.owner]: uuidv7(), [users.viewer]: uuidv7() };
  const org = uuidv7();
  const workspace = uuidv7();
  /** A workspace of another org the user is not a member of. */
  const foreign = { org: uuidv7(), workspace: uuidv7(), job: uuidv7(), search: uuidv7() };
  let app: NestFastifyApplication | undefined;
  let keys: TestKeys;

  const api = () => {
    if (!app) throw new Error('app not started');
    return app;
  };
  const call = async (
    method: 'GET' | 'POST',
    url: string,
    userId: string,
    payload?: unknown,
    extraHeaders: Record<string, string> = {},
  ) =>
    api().inject({
      method,
      url,
      headers: {
        authorization: `Bearer ${await keys.sign({
          sub: userId,
          email: `${userId}@test.invalid`,
          session_id: sessions[userId],
        })}`,
        'x-workspace-id': workspace,
        ...(payload ? { 'content-type': 'application/json' } : {}),
        ...extraHeaders,
      },
      ...(payload ? { payload } : {}),
    });
  const balance = async (): Promise<number> => {
    const [row] = await owner<{ credits_balance: string }[]>`
      select credits_balance from app.organizations where id = ${org}`;
    return Number(row?.credits_balance ?? -1);
  };

  beforeAll(async () => {
    keys = await createTestKeys();
    for (const [label, id] of Object.entries(users)) {
      await owner`insert into auth.users (id, email, aud, role, email_confirmed_at)
                  values (${id}, ${`research-${label}-${id}@test.invalid`}, 'authenticated', 'authenticated', now())`;
      await owner`insert into auth.sessions (id, user_id) values (${sessions[id] ?? ''}, ${id})`;
    }
    // Enough for every run in this file; the ledger row keeps balance = sum(ledger).
    await owner`insert into app.organizations (id, name, slug, credits_balance)
                values (${org}, 'Research Co', ${`research-${org.slice(-12)}`}, 600)`;
    await owner`insert into app.credit_ledger (id, org_id, delta, balance_after, reason)
                values (${uuidv7()}, ${org}, 600, 600, 'grant')`;
    await owner`insert into app.workspaces (id, org_id, name) values (${workspace}, ${org}, 'Default')`;
    await owner`insert into app.memberships (id, org_id, workspace_id, user_id, role)
                values (${uuidv7()}, ${org}, ${workspace}, ${users.owner}, 'owner')`;
    await owner`insert into app.memberships (id, org_id, workspace_id, user_id, role)
                values (${uuidv7()}, ${org}, ${workspace}, ${users.viewer}, 'viewer')`;
    // Another org with its own job: nothing about it may be readable or cancellable here.
    await owner`insert into app.organizations (id, name, slug, credits_balance)
                values (${foreign.org}, 'Other Co', ${`other-${foreign.org.slice(-12)}`}, 100)`;
    await owner`insert into app.workspaces (id, org_id, name) values (${foreign.workspace}, ${foreign.org}, 'Default')`;
    await owner`insert into app.searches (id, org_id, workspace_id, raw_query, spec, spec_version)
                values (${foreign.search}, ${foreign.org}, ${foreign.workspace}, 'foreign query', '{}', 1)`;
    await owner`insert into app.research_jobs (id, org_id, workspace_id, search_id)
                values (${foreign.job}, ${foreign.org}, ${foreign.workspace}, ${foreign.search})`;
    app = await createTestApp(
      { dbOk: true, redisOk: true, storageOk: true },
      { keySet: keys.keySet, realDb: true, realRedis: true },
    );
  });

  afterAll(async () => {
    const ids = Object.values(users);
    try {
      await app?.close();
    } finally {
      await owner`delete from app.organizations where id = any(${[org, foreign.org]}::uuid[])`;
      await owner`delete from app.audit_logs where actor_user_id = any(${ids}::uuid[])`;
      await owner`delete from auth.users where id = any(${ids}::uuid[])`;
      await redis.del(stream);
      redis.disconnect();
      await owner.end();
    }
  });

  it('creates a search + job, reserves credits and queues the envelope', async () => {
    const before = await balance();
    const res = await call('POST', '/app/research', users.owner, {
      rawQuery: 'restaurants in delhi',
      spec: SPEC,
    });
    expect(res.statusCode, res.body).toBe(202);
    const body = res.json<{ jobId: string; credits: { reserved: number; balance: number } }>();
    // standard depth = 3 credits per lead x 10 results
    expect(body.credits.reserved).toBe(30);
    expect(body.credits.balance).toBe(before - 30);
    expect(await balance()).toBe(before - 30);

    const [job] = await owner<{ status: string; search_id: string; credits_reserved: number }[]>`
      select status, search_id, credits_reserved from app.research_jobs where id = ${body.jobId}`;
    expect(job).toMatchObject({ status: 'queued', credits_reserved: 30 });
    const [search] = await owner<{ raw_query: string }[]>`
      select raw_query from app.searches where id = ${job?.search_id ?? ''}`;
    expect(search?.raw_query).toBe('restaurants in delhi');

    // The planner envelope is queued with the job's budget (task 2.10 consumes it).
    const entries = await redis.xrange(stream, '-', '+');
    const envelopes = entries.map(
      ([, fields]) => JSON.parse(fields[1] ?? '{}') as Record<string, unknown>,
    );
    const queued = envelopes.find((e) => e.job_id === body.jobId);
    expect(queued).toMatchObject({ type: 'research.plan', research_job_id: body.jobId });
    expect(queued?.budget).toMatchObject({ credits_remaining: 30 });
  });

  it('shows status, spec and credits, and lists history newest first', async () => {
    const created = await call('POST', '/app/research', users.owner, {
      rawQuery: 'cafes in pune',
      spec: SPEC,
    });
    const { jobId } = created.json<{ jobId: string }>();

    const detail = await call('GET', `/app/research/${jobId}`, users.owner);
    expect(detail.statusCode).toBe(200);
    expect(detail.json()).toMatchObject({
      id: jobId,
      status: 'queued',
      depth: 'standard',
      rawQuery: 'cafes in pune',
      credits: { reserved: 30, used: 0 },
    });

    const history = await call('GET', '/app/research?limit=1', users.owner);
    const page = history.json<{
      items: { id: string; rawQuery: string }[];
      nextCursor: string | null;
    }>();
    expect(page.items[0]).toMatchObject({ id: jobId, rawQuery: 'cafes in pune' });
    expect(page.nextCursor).toBe(jobId);
    const older = await call(
      'GET',
      `/app/research?limit=5&cursor=${page.nextCursor ?? ''}`,
      users.owner,
    );
    expect(older.json<{ items: { id: string }[] }>().items[0]?.id).not.toBe(jobId);
  });

  it('cancels a queued job and returns the unused credits', async () => {
    const created = await call('POST', '/app/research', users.owner, {
      rawQuery: 'salons in jaipur',
      spec: SPEC,
    });
    const { jobId } = created.json<{ jobId: string }>();
    const held = await balance();

    const cancelled = await call('POST', `/app/research/${jobId}/cancel`, users.owner);
    expect(cancelled.statusCode).toBe(200);
    expect(cancelled.json()).toMatchObject({ status: 'cancelled', credits: { used: 0 } });
    expect(await balance()).toBe(held + 30);
    // Cancelling again is a no-op, not an error or a second refund.
    const again = await call('POST', `/app/research/${jobId}/cancel`, users.owner);
    expect(again.statusCode).toBe(200);
    expect(await balance()).toBe(held + 30);
  });

  it('refuses a run the balance cannot cover with 402 and creates nothing', async () => {
    const big: ResearchSpec = { ...SPEC, limits: { max_results: 10000, max_credits: 999_999 } };
    const before = await balance();
    const [jobsBefore] = await owner<
      { n: number }[]
    >`select count(*)::int as n from app.research_jobs where org_id = ${org}`;
    const res = await call('POST', '/app/research', users.owner, {
      rawQuery: 'everything everywhere',
      spec: big,
    });
    expect(res.statusCode).toBe(402);
    expect(res.json()).toMatchObject({ code: 'credits.insufficient' });
    expect(await balance()).toBe(before);
    const [jobsAfter] = await owner<
      { n: number }[]
    >`select count(*)::int as n from app.research_jobs where org_id = ${org}`;
    expect(jobsAfter?.n).toBe(jobsBefore?.n);
  });

  it('rejects an invalid spec and an unknown industry before spending anything', async () => {
    const before = await balance();
    const bad = await call('POST', '/app/research', users.owner, {
      rawQuery: 'x',
      spec: { ...SPEC, depth: 'ultra' },
    });
    expect(bad.statusCode).toBe(400);

    const unknown = await call('POST', '/app/research', users.owner, {
      rawQuery: 'x',
      spec: {
        ...SPEC,
        filters: {
          ...SPEC.filters,
          industry: { include: [], exclude: [], taxonomy_ids: ['not-a-real-industry'] },
        },
      },
    });
    expect(unknown.statusCode).toBe(422);
    expect(unknown.json()).toMatchObject({ code: 'research.unknown_industry' });
    expect(await balance()).toBe(before);
  });

  it('lets a viewer read history but never spend or cancel', async () => {
    const list = await call('GET', '/app/research', users.viewer);
    expect(list.statusCode).toBe(200);
    const create = await call('POST', '/app/research', users.viewer, { rawQuery: 'x', spec: SPEC });
    expect(create.statusCode).toBe(403);
    expect(create.json()).toMatchObject({ code: 'rbac.forbidden' });
  });

  it('a retry with the same Idempotency-Key replays the first answer, it does not spend again', async () => {
    const before = await balance();
    const headers = { 'idempotency-key': `key-${uuidv7()}` };
    const first = await call(
      'POST',
      '/app/research',
      users.owner,
      { rawQuery: 'retry me', spec: SPEC },
      headers,
    );
    const second = await call(
      'POST',
      '/app/research',
      users.owner,
      { rawQuery: 'retry me', spec: SPEC },
      headers,
    );
    expect(first.statusCode).toBe(202);
    expect(second.statusCode).toBe(202);
    expect(second.json<{ jobId: string }>().jobId).toBe(first.json<{ jobId: string }>().jobId);
    expect(await balance()).toBe(before - 30);
  });

  it('two runs starting at once queue instead of deadlocking', async () => {
    const before = await balance();
    const both = await Promise.all([
      call('POST', '/app/research', users.owner, { rawQuery: 'parallel a', spec: SPEC }),
      call('POST', '/app/research', users.owner, { rawQuery: 'parallel b', spec: SPEC }),
    ]);
    expect(both.map((r) => r.statusCode)).toEqual([202, 202]);
    expect(await balance()).toBe(before - 60);
  });

  it('cancelling raises a stop flag the worker can see', async () => {
    const created = await call('POST', '/app/research', users.owner, {
      rawQuery: 'stop me',
      spec: SPEC,
    });
    const { jobId } = created.json<{ jobId: string }>();
    expect(await redis.get(`research:cancelled:${jobId}`)).toBeNull();
    await call('POST', `/app/research/${jobId}/cancel`, users.owner);
    expect(await redis.get(`research:cancelled:${jobId}`)).toBe('1');
  });

  it('never reveals a job of another org, and a viewer cannot cancel', async () => {
    const detail = await call('GET', `/app/research/${foreign.job}`, users.owner);
    expect(detail.statusCode).toBe(404);
    const cancel = await call('POST', `/app/research/${foreign.job}/cancel`, users.owner);
    expect(cancel.statusCode).toBe(404);
    const [row] = await owner<
      { status: string }[]
    >`select status from app.research_jobs where id = ${foreign.job}`;
    expect(row?.status).toBe('queued');

    const mine = await call('POST', '/app/research', users.owner, {
      rawQuery: 'viewer test',
      spec: SPEC,
    });
    const viewerCancel = await call(
      'POST',
      `/app/research/${mine.json<{ jobId: string }>().jobId}/cancel`,
      users.viewer,
    );
    expect(viewerCancel.statusCode).toBe(403);
  });

  it('hides jobs of other workspaces (404, not 403)', async () => {
    const res = await call('GET', `/app/research/${uuidv7()}`, users.owner);
    expect(res.statusCode).toBe(404);
    expect(res.json()).toMatchObject({ code: 'research.not_found' });
  });
});
