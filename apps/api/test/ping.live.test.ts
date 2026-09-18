/**
 * Phase 1 acceptance, across languages (task 1.10): API publishes system.ping -> the real Python
 * worker processes it -> DB row updated -> progress over Redis -> SSE. Also: killing the worker
 * mid-job and restarting completes the job (stale claim), and 5 failures land in the DLQ.
 * Needs DATABASE_URL(+_MIGRATIONS, _WORKERS), REDIS_URL and the workers venv (`uv sync`).
 */
import { type ChildProcess, spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

import type { NestFastifyApplication } from '@nestjs/platform-fastify';
import { Redis } from 'ioredis';
import postgres from 'postgres';
import { uuidv7 } from 'uuidv7';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { createTestApp } from './helpers/test-app';
import { createTestKeys, type TestKeys } from './helpers/tokens';

const workersDir = resolve(__dirname, '..', '..', '..', 'services', 'workers');
const python = resolve(
  workersDir,
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
);
const configured = Boolean(
  process.env.DATABASE_URL_MIGRATIONS &&
  process.env.DATABASE_URL &&
  process.env.DATABASE_URL_WORKERS &&
  process.env.REDIS_URL,
);
if (configured && process.env.CI && !existsSync(python)) {
  throw new Error(`workers venv missing at ${python}: run "uv sync" in services/workers`);
}
const live = configured && existsSync(python);
// A stream pool of its own: dev workers or parallel runs can never take this run's jobs.
const POOL = `pinglive_${Date.now().toString(36)}`;
const STREAM = `jobs:${POOL}`;

interface SseEvent {
  event: string;
  data: { status?: string; [key: string]: unknown };
}

function parseSse(body: string): SseEvent[] {
  return body
    .split('\n\n')
    .filter((chunk) => chunk.startsWith('event:'))
    .map((chunk) => {
      const [eventLine, dataLine] = chunk.split('\n');
      return {
        event: (eventLine ?? '').replace('event: ', ''),
        data: JSON.parse((dataLine ?? '').replace('data: ', '')) as SseEvent['data'],
      };
    });
}

async function waitFor<T>(probe: () => Promise<T | undefined>, timeoutMs: number): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await probe();
    if (value !== undefined) return value;
    if (Date.now() > deadline) throw new Error('timed out waiting for condition');
    await new Promise((r) => setTimeout(r, 200));
  }
}

describe.skipIf(!live)('system.ping round trip with the real worker', () => {
  const db = postgres(process.env.DATABASE_URL_MIGRATIONS ?? '', { max: 1, prepare: false });
  const redis = new Redis(process.env.REDIS_URL ?? '');
  const user = uuidv7();
  const session = uuidv7();
  const org = uuidv7();
  const workspace = uuidv7();
  let worker: ChildProcess | undefined;
  let app: NestFastifyApplication | undefined;
  let keys: TestKeys;

  const startWorker = (): ChildProcess => {
    const child = spawn(python, ['-m', 'app.main'], {
      cwd: workersDir,
      env: {
        ...process.env,
        NODE_ENV: 'test',
        LOG_LEVEL: 'warning',
        WORKER_POOLS: POOL,
        WORKER_NAME: `ping-live-${String(Date.now())}`,
        JOB_VISIBILITY_TIMEOUT_MS: '1500',
        JOB_RECLAIM_INTERVAL_MS: '300',
        JOB_RETRY_BASE_DELAY_MS: '50',
        PYTHONUNBUFFERED: '1',
      },
      stdio: ['ignore', 'ignore', 'inherit'],
    });
    return child;
  };
  const stopWorker = async (): Promise<void> => {
    const child = worker;
    worker = undefined;
    if (!child || child.exitCode !== null) return;
    const exited = new Promise((r) => child.once('exit', r));
    child.kill('SIGKILL'); // hard kill: no cleanup, exactly like a crash
    await exited;
  };
  const api = () => {
    if (!app) throw new Error('app not started');
    return app;
  };
  const headers = async () => ({
    authorization: `Bearer ${await keys.sign({ sub: user, session_id: session })}`,
    'x-workspace-id': workspace,
  });
  const createPing = async (body: Record<string, unknown>): Promise<string> => {
    const res = await api().inject({
      method: 'POST',
      url: '/app/dev/ping-job',
      headers: await headers(),
      payload: body,
    });
    expect(res.statusCode, res.body).toBe(202);
    const { jobId } = res.json<{ jobId: string }>();
    return jobId;
  };
  const status = async (jobId: string) =>
    (
      await api().inject({
        method: 'GET',
        url: `/app/dev/ping-job/${jobId}`,
        headers: await headers(),
      })
    ).json<{ status: string; attempts: number; errorClass: string | null; result: unknown }>();

  beforeAll(async () => {
    keys = await createTestKeys();
    await db`insert into auth.users (id, email, aud, role, email_confirmed_at)
             values (${user}, ${`ping-${user}@test.invalid`}, 'authenticated', 'authenticated', now())`;
    await db`insert into auth.sessions (id, user_id) values (${session}, ${user})`;
    await db`insert into app.organizations (id, name, slug) values (${org}, 'Ping Test', ${`rls-ping-${org.slice(-12)}`})`;
    await db`insert into app.workspaces (id, org_id, name) values (${workspace}, ${org}, 'Default')`;
    await db`insert into app.memberships (id, org_id, workspace_id, user_id, role)
             values (${uuidv7()}, ${org}, ${workspace}, ${user}, 'owner')`;
    worker = startWorker();
    // Wait until the worker's consumer group is live on the per-run stream.
    await waitFor(async () => {
      const groups = (await redis.xinfo('GROUPS', STREAM).catch(() => [])) as unknown[];
      return groups.length > 0 ? true : undefined;
    }, 30_000);
    process.env.JOBS_SYSTEM_POOL = POOL;
    process.env.PROGRESS_STREAM_MAX_MS = '30000';
    app = await createTestApp(
      { dbOk: true, redisOk: true, storageOk: true },
      { keySet: keys.keySet, realDb: true, realRedis: true },
    );
  }, 60_000);

  afterAll(async () => {
    try {
      await stopWorker();
      await app?.close();
      await redis.del(STREAM, `dlq:${STREAM}`, `jobs:delayed:${STREAM}`);
      delete process.env.JOBS_SYSTEM_POOL;
      delete process.env.PROGRESS_STREAM_MAX_MS;
    } finally {
      await db`delete from app.organizations where id = ${org}`;
      await db`delete from app.audit_logs where actor_user_id = ${user} or org_id = ${org}`;
      await db`delete from auth.users where id = ${user}`;
      await db.end();
      redis.disconnect();
    }
  }, 60_000);

  it('streams state -> progress -> done over SSE and persists the result', async () => {
    const jobId = await createPing({ message: 'hello', steps: 4, delayMs: 2000 });
    const res = await api().inject({
      method: 'GET',
      url: `/app/dev/ping-job/${jobId}/events`,
      headers: await headers(),
    });
    expect(res.statusCode).toBe(200);
    expect(res.headers['content-type']).toContain('text/event-stream');
    const events = parseSse(res.body);
    expect(events[0]?.event).toBe('state');
    const progress = events.filter((e) => e.event === 'progress');
    expect(progress.length).toBeGreaterThan(0);
    expect(progress.at(-1)?.data.status).toBe('completed');
    expect(events.at(-1)).toMatchObject({ event: 'done', data: { status: 'completed' } });
    expect(await status(jobId)).toMatchObject({
      status: 'completed',
      result: { echo: 'hello', attempts: 1 },
    });
  }, 30_000);

  it('a job interrupted by a worker crash completes after restart (stale claim)', async () => {
    const jobId = await createPing({ steps: 6, delayMs: 6000 });
    await waitFor(
      async () => ((await status(jobId)).status === 'running' ? true : undefined),
      15_000,
    );
    await stopWorker(); // killed mid-job
    expect((await status(jobId)).status).toBe('running');
    worker = startWorker();
    const done = await waitFor(async () => {
      const s = await status(jobId);
      return s.status === 'completed' || s.status === 'failed' ? s : undefined;
    }, 40_000);
    // Same delivery attempt taken over by the new worker, not a retry.
    expect(done).toMatchObject({ status: 'completed', result: { attempts: 1 } });
  }, 60_000);

  it('a job that fails 5 times lands in the DLQ and is marked failed', async () => {
    const jobId = await createPing({ failTimes: 5, delayMs: 0 });
    const failed = await waitFor(async () => {
      const s = await status(jobId);
      return s.status === 'failed' ? s : undefined;
    }, 40_000);
    expect(failed).toMatchObject({ status: 'failed', attempts: 5, errorClass: 'transient' });
    const dlq = await redis.xrange(`dlq:${STREAM}`, '-', '+');
    const entry = dlq.find(([, fields]) => fields.join(' ').includes(jobId));
    expect(entry).toBeDefined();
    const fields = entry?.[1] ?? [];
    expect(fields[fields.indexOf('error_class') + 1]).toBe('transient');
    expect(fields[fields.indexOf('attempts') + 1]).toBe('5');
  }, 60_000);
});
