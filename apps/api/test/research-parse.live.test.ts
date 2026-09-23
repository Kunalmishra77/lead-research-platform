/**
 * The parse endpoint against the dev/test database and Redis (Phase 2 task 2.4, ADR-0005).
 *
 * No worker runs here: the test plays one, which is the point — it exercises the real envelope,
 * the real reply contract and the real failure paths, including the one where nobody answers.
 * Skipped without DATABASE_URL + DATABASE_URL_MIGRATIONS + REDIS_URL.
 */
import type { NestFastifyApplication } from '@nestjs/platform-fastify';
import { Redis } from 'ioredis';
import postgres from 'postgres';
import { uuidv7 } from 'uuidv7';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';

import { createTestApp } from './helpers/test-app';
import { createTestKeys, type TestKeys } from './helpers/tokens';

const ownerUrl = process.env.DATABASE_URL_MIGRATIONS;
const live = Boolean(ownerUrl && process.env.DATABASE_URL && process.env.REDIS_URL);

const POOL = `parse_test_${Date.now().toString(36)}`;
const TIMEOUT_MS = 6_000;
const RATE_PER_MINUTE = 3;

/** What a worker would build from "restaurants in Delhi with a website". */
const SPEC = {
  spec_version: 1,
  entity: 'company',
  intent: 'prospecting',
  filters: {
    industry: { include: ['restaurant'] },
    location: { country: 'IN', states: [], cities: ['Delhi'] },
    has_website: true,
  },
  fields: ['name', 'category', 'address', 'city', 'phone', 'website'],
  depth: 'standard',
  limits: { max_results: 250, max_credits: 500 },
};

const okReply = (jobId: string) => ({
  reply_version: 1,
  job_id: jobId,
  ok: true,
  result: {
    spec: SPEC,
    feasibility: [
      { filter: 'filters.industry', mode: 'directly_searchable', note: 'Searched as a category.' },
      { filter: 'filters.has_website', mode: 'post_filter', note: 'Checked after each result.' },
    ],
    needs_confirmation: false,
    ambiguities: [],
    unsupported: [],
    confidence: 0.97,
    provenance: {
      model: 'gpt-5.4-mini-2026-03-17',
      prompt_version: 'spec_parse@v4',
      observed_at: new Date().toISOString(),
      cached: false,
    },
  },
});

describe.skipIf(!live)('research parse endpoint', () => {
  const owner = postgres(ownerUrl ?? '', { max: 1, prepare: false, onnotice: () => undefined });
  const redis = new Redis(process.env.REDIS_URL ?? '');
  const stream = `jobs:${POOL}`;
  const group = `workers:${POOL}`;
  const users = { owner: uuidv7(), viewer: uuidv7() };
  const sessions: Record<string, string> = { [users.owner]: uuidv7(), [users.viewer]: uuidv7() };
  const org = uuidv7();
  const workspace = uuidv7();
  let app: NestFastifyApplication | undefined;
  let keys: TestKeys;

  const api = () => {
    if (!app) throw new Error('app not started');
    return app;
  };

  const call = async (userId: string, payload: Record<string, unknown>) =>
    api().inject({
      method: 'POST',
      url: '/app/research/parse',
      headers: {
        authorization: `Bearer ${await keys.sign({
          sub: userId,
          email: `${userId}@test.invalid`,
          session_id: sessions[userId],
        })}`,
        'x-workspace-id': workspace,
        'content-type': 'application/json',
      },
      payload,
    });

  /**
   * Waits for the API's envelope, then answers it the way a worker would. It reads forward from
   * a cursor rather than clearing the stream, because deleting it would take the consumer group
   * with it and the API checks for one before it publishes.
   */
  let cursor = '-';
  const playWorker = async (reply: (jobId: string) => unknown): Promise<Record<string, string>> => {
    const deadline = Date.now() + TIMEOUT_MS;
    while (Date.now() < deadline) {
      const entries = await redis.xrange(stream, cursor, '+');
      const last = entries.at(-1);
      if (last) {
        cursor = `(${last[0]}`;
        const envelope = JSON.parse(last[1][1] ?? '{}') as {
          job_id: string;
          payload: { reply_to: string };
        };
        const answer = reply(envelope.job_id);
        if (answer !== null) {
          await redis.lpush(envelope.payload.reply_to, JSON.stringify(answer));
          await redis.expire(envelope.payload.reply_to, 60);
        }
        return envelope as unknown as Record<string, string>;
      }
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    throw new Error('the API never published a parse envelope');
  };

  /** The API refuses to wait when nothing is consuming, so the test registers a consumer. */
  const joinPool = async () => {
    await redis.xgroup('CREATE', stream, group, '$', 'MKSTREAM').catch(() => undefined); // BUSYGROUP: already there
    await redis.xreadgroup(
      'GROUP',
      group,
      'test-worker',
      'COUNT',
      1,
      'BLOCK',
      1,
      'STREAMS',
      stream,
      '>',
    );
  };

  beforeAll(async () => {
    process.env.JOBS_INTERACTIVE_POOL = POOL;
    process.env.PARSE_TIMEOUT_MS = String(TIMEOUT_MS);
    process.env.PARSE_RATE_PER_MINUTE = String(RATE_PER_MINUTE);
    keys = await createTestKeys();
    for (const [label, id] of Object.entries(users)) {
      await owner`insert into auth.users (id, email, aud, role, email_confirmed_at)
                  values (${id}, ${`parse-${label}-${id}@test.invalid`}, 'authenticated', 'authenticated', now())`;
      await owner`insert into auth.sessions (id, user_id) values (${sessions[id] ?? ''}, ${id})`;
    }
    await owner`insert into app.organizations (id, name, slug, credits_balance)
                values (${org}, 'Parse Co', ${`parse-${org.slice(-12)}`}, 500)`;
    await owner`insert into app.credit_ledger (id, org_id, delta, balance_after, reason)
                values (${uuidv7()}, ${org}, 500, 500, 'grant')`;
    await owner`insert into app.workspaces (id, org_id, name) values (${workspace}, ${org}, 'Default')`;
    await owner`insert into app.memberships (id, org_id, workspace_id, user_id, role)
                values (${uuidv7()}, ${org}, ${workspace}, ${users.owner}, 'owner')`;
    await owner`insert into app.memberships (id, org_id, workspace_id, user_id, role)
                values (${uuidv7()}, ${org}, ${workspace}, ${users.viewer}, 'viewer')`;
    app = await createTestApp(
      { dbOk: true, redisOk: true, storageOk: true },
      { keySet: keys.keySet, realDb: true, realRedis: true },
    );
    await joinPool();
  });

  afterEach(async () => {
    const keysToClear = await redis.keys(`parse:rate:${org}:*`);
    if (keysToClear.length > 0) await redis.del(...keysToClear);
  });

  afterAll(async () => {
    const ids = Object.values(users);
    try {
      await app?.close();
    } finally {
      delete process.env.JOBS_INTERACTIVE_POOL;
      delete process.env.PARSE_TIMEOUT_MS;
      delete process.env.PARSE_RATE_PER_MINUTE;
      await owner`delete from app.organizations where id = ${org}`;
      await owner`delete from app.audit_logs where actor_user_id = any(${ids}::uuid[])`;
      await owner`delete from auth.users where id = any(${ids}::uuid[])`;
      await redis.del(stream);
      redis.disconnect();
      await owner.end();
    }
  });

  it('returns the spec a worker produced, priced with this org rate card', async () => {
    const [response, envelope] = await Promise.all([
      call(users.owner, { rawQuery: 'restaurants in Delhi with a website' }),
      playWorker(okReply),
    ]);

    expect(response.statusCode).toBe(200);
    const body = response.json<{
      spec: { filters: { location: { cities: string[] } } };
      needsConfirmation: boolean;
      provenance: { promptVersion: string };
      estimate: { maxResults: number; reserve: number };
      feasibility: unknown[];
    }>();
    expect(body.spec.filters.location.cities).toEqual(['Delhi']);
    expect(body.needsConfirmation).toBe(false);
    expect(body.provenance.promptVersion).toBe('spec_parse@v4');
    // The estimate comes from the same service the create endpoint reserves against.
    expect(body.estimate.maxResults).toBe(250);
    expect(body.estimate.reserve).toBeGreaterThan(0);
    expect(body.feasibility).toHaveLength(2);

    // The envelope is attributed to the org but to no job: none exists yet (ADR-0005).
    const published = envelope as unknown as {
      type: string;
      org_id: string;
      research_job_id: string | null;
      payload: { raw_query: string; reply_to: string };
    };
    expect(published.type).toBe('research.parse');
    expect(published.org_id).toBe(org);
    expect(published.research_job_id).toBeNull();
    expect(published.payload.raw_query).toBe('restaurants in Delhi with a website');
    expect(published.payload.reply_to.startsWith('rpc:reply:')).toBe(true);
  });

  it('reports a classified worker failure instead of a generic error', async () => {
    const [response] = await Promise.all([
      call(users.owner, { rawQuery: 'asdfgh' }),
      playWorker((jobId) => ({
        reply_version: 1,
        job_id: jobId,
        ok: false,
        error: { error_class: 'parse_failed', message: 'output did not match its schema' },
      })),
    ]);

    expect(response.statusCode).toBe(422);
    expect(response.json<{ detail: string }>().detail).toContain('did not match');
  });

  it('waits for a worker that takes longer than the shared client would allow', async () => {
    const [response] = await Promise.all([
      call(users.owner, { rawQuery: 'salons in Kolkata' }),
      (async () => {
        // Longer than the 3 s commandTimeout the shared Redis client sets. A connection that
        // inherited it would abort here and report the queue as down, while the worker
        // finished the parse and billed the org for it.
        await new Promise((resolve) => setTimeout(resolve, 4_000));
        return playWorker(okReply);
      })(),
    ]);

    expect(response.statusCode).toBe(200);
    expect(response.json<{ spec: { intent: string } }>().spec.intent).toBe('prospecting');
  }, 20_000);

  it('waits out the timeout when a worker is listening but never answers', async () => {
    const started = Date.now();
    const response = await call(users.owner, { rawQuery: 'cafes in Pune' });

    expect(response.statusCode).toBe(503);
    expect(response.json<{ code: string }>().code).toBe('research.parse_unavailable');
    // It waits for the configured timeout rather than failing instantly or hanging.
    expect(Date.now() - started).toBeGreaterThanOrEqual(TIMEOUT_MS - 250);
  }, 20_000);

  it('fails in milliseconds when nothing is consuming the pool at all', async () => {
    await redis.del(stream);
    const started = Date.now();

    const response = await call(users.owner, { rawQuery: 'cafes in Nashik' });

    // A deployment that forgot WORKER_POOLS=...,interactive looks exactly like this, and
    // making every user wait 20 seconds to find out would be the wrong way to tell them.
    expect(response.statusCode).toBe(503);
    expect(response.json<{ code: string }>().code).toBe('research.parse_unavailable');
    expect(Date.now() - started).toBeLessThan(1_000);
    await joinPool();
    cursor = '-';
  });

  it('refuses a reply that does not match the contract', async () => {
    const [response] = await Promise.all([
      call(users.owner, { rawQuery: 'gyms in Mumbai' }),
      playWorker((jobId) => ({ reply_version: 1, job_id: jobId, ok: true, result: { spec: {} } })),
    ]);

    expect(response.statusCode).toBe(502);
    expect(response.json<{ code: string }>().code).toBe('research.parse_invalid_reply');
  });

  it('refuses a well-formed reply whose spec is not one', async () => {
    const [response] = await Promise.all([
      call(users.owner, { rawQuery: 'tailors in Ludhiana' }),
      playWorker((jobId) => {
        const reply = okReply(jobId);
        // Everything the reply contract asks for, but the spec itself has no limits: reading
        // `spec.limits.max_results` off this used to be a 500.
        return { ...reply, result: { ...reply.result, spec: { spec_version: 1 } } };
      }),
    ]);

    expect(response.statusCode).toBe(502);
    expect(response.json<{ code: string }>().code).toBe('research.parse_invalid_reply');
  });

  it('rejects an empty query before anything is published', async () => {
    const before = await redis.xrange(stream, cursor, '+');
    const response = await call(users.owner, { rawQuery: '   ' });

    expect(response.statusCode).toBe(400);
    expect(await redis.xrange(stream, cursor, '+')).toHaveLength(before.length);
  });

  it('needs permission to spend, because parsing costs tokens', async () => {
    const before = await redis.xrange(stream, cursor, '+');
    const response = await call(users.viewer, { rawQuery: 'dentists in Jaipur' });

    expect(response.statusCode).toBe(403);
    expect(await redis.xrange(stream, cursor, '+')).toHaveLength(before.length);
  });

  it('limits how often one workspace may parse', async () => {
    for (let i = 0; i < RATE_PER_MINUTE; i += 1) {
      await Promise.all([
        call(users.owner, { rawQuery: `bakeries in Surat ${i}` }),
        playWorker(okReply),
      ]);
    }

    const response = await call(users.owner, { rawQuery: 'one too many' });

    // Parsing spends our tokens and none of the user's credits, so this is the only brake.
    expect(response.statusCode).toBe(429);
    expect(response.json<{ code: string }>().code).toBe('research.parse_rate_limited');
    expect(await redis.xrange(stream, cursor, '+')).toHaveLength(0);
  });
});
